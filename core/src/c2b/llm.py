"""LLMClient 适配层：OpenAI 兼容 API + 结构化 JSON 输出 + 重试/缓存/成本计数。

统一入口，MVP 阶段支持 OpenAI 兼容服务（DeepSeek/OpenAI/Kimi 等），
预留本地模型（Ollama）切换位。
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .security import UnsafeUrlError, validate_outbound_url

# LLM 结果缓存：统一放 ~/.c2b/llm_cache（不依赖 DB URL——之前按 DB URL 拼路径
# 对 PostgreSQL URL 会生成非法目录，导致缓存写入失败）
_CACHE_DIR = Path.home() / ".c2b" / "llm_cache"
try:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(_CACHE_DIR, 0o700)
except OSError:
    pass

_PROMPT_CACHE_VERSION = "2026-08-08.1"
_CACHE_LOCK = threading.Lock()
_USAGE_LOCK = threading.Lock()

# 成本计数（进程内累计，按 token 估算）
USAGE = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "failed": 0}


def _record_usage(*, requests: int = 0, prompt: int = 0,
                  completion: int = 0, failed: int = 0) -> None:
    with _USAGE_LOCK:
        USAGE["requests"] += requests
        USAGE["prompt_tokens"] += prompt
        USAGE["completion_tokens"] += completion
        USAGE["failed"] += failed


def _cache_fingerprint(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    schema: dict | None,
    temperature: float,
    prompt_version: str = _PROMPT_CACHE_VERSION,
) -> str:
    """Build a cache key from every input that can change model output."""
    payload = {
        "version": prompt_version,
        "base_url": base_url,
        "model": model,
        "system": system,
        "user": user,
        "schema": schema,
        "temperature": temperature,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


class LLMError(Exception):
    pass


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _validate_json_schema(value: Any, schema: dict | None, path: str = "$") -> None:
    """Validate the JSON-Schema subset used by this project.

    Avoiding a new dependency keeps the offline build small while still
    enforcing object/array/scalar types, required fields, enums and the
    ``additionalProperties`` contracts present in every semantic schema.
    """
    if not schema:
        if not isinstance(value, dict):
            raise LLMError(f"LLM JSON 根节点必须是对象，实际位于 {path}")
        return

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    expected_types = [item for item in expected_types if item]
    if expected_types and not any(
        _schema_type_matches(value, item) for item in expected_types
    ):
        raise LLMError(
            f"LLM JSON 字段 {path} 类型错误：期望 {'/'.join(expected_types)}"
        )
    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"LLM JSON 字段 {path} 不在允许值范围内")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise LLMError(f"LLM JSON 字段 {path} 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise LLMError(f"LLM JSON 字段 {path} 大于最大值 {schema['maximum']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise LLMError(f"LLM JSON 字段 {path} 缺少必填项: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise LLMError(
                    f"LLM JSON 字段 {path} 包含未知项: {', '.join(sorted(extras))}"
                )
        for key, child in value.items():
            if key in properties:
                _validate_json_schema(child, properties[key], f"{path}.{key}")
    elif isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            _validate_json_schema(child, schema["items"], f"{path}[{index}]")


def _parse_json_object(content: str) -> dict:
    """Parse a JSON object, tolerating providers that add a Markdown fence."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(text[start:end + 1])
    if not isinstance(result, dict):
        raise LLMError("LLM JSON 根节点必须是对象")
    return result


class LLMClient:
    """OpenAI 兼容客户端（api 模式）或内置模拟（mock 模式）。

    mock 模式：不调用网络，从代码特征确定性构造输出，用于管线验证。
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 300.0,
                 max_retries: int = 3, cache: bool = True,
                 mode: str | None = None):
        from . import settings_store
        st = settings_store.load_settings()
        self.mode = mode or st["llm_mode"]
        if self.mode not in {"api", "mock"}:
            raise LLMError(f"未知 LLM 模式: {self.mode}")
        configured_url = (base_url or st["llm_base_url"]).rstrip("/")
        allow_private = os.environ.get("C2B_ALLOW_PRIVATE_LLM", "").lower() in {
            "1", "true", "yes", "on",
        }
        if self.mode == "api":
            try:
                configured_url = validate_outbound_url(
                    configured_url,
                    purpose="llm",
                    allow_private=allow_private,
                ).rstrip("/")
            except UnsafeUrlError as exc:
                raise LLMError(f"LLM Base URL 不安全: {exc}") from exc
        self.base_url = configured_url
        self.api_key = api_key or st["llm_api_key"]
        self.model = model or st["llm_model"]
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = cache
        if self.mode == "api" and not self.api_key and allow_private:
            # Local OpenAI-compatible servers commonly ignore Authorization.
            self.api_key = "local"
        if self.mode == "api" and not self.api_key:
            raise LLMError(
                "缺少 API Key。配置方式（任选其一）：\n"
                "  1) 工作台右上角「LLM 设置」填写并保存\n"
                "  2) 复制 c2b/.env.example 为 .env 并填写 C2B_LLM_API_KEY\n"
                "  3) export C2B_LLM_API_KEY=sk-xxx\n"
                "  4) 临时验证管线可设置 C2B_LLM_MODE=mock 使用内置模拟")

    def chat_json(self, system: str, user: str,
                  schema: dict | None = None, temperature: float = 0.2) -> dict:
        """结构化 JSON 输出（带缓存与重试）。返回 dict；失败抛 LLMError。"""
        if self.mode == "mock":
            result = self._mock_json(system, user, schema)
            _validate_json_schema(result, schema)
            return result
        key = _cache_fingerprint(
            base_url=self.base_url,
            model=self.model,
            system=system,
            user=user,
            schema=schema,
            temperature=temperature,
        )
        cpath = _cache_path(key)
        if self.cache:
            if cpath.exists():
                try:
                    cached = json.loads(cpath.read_text(encoding="utf-8"))
                    _validate_json_schema(cached, schema)
                    return cached
                except (json.JSONDecodeError, OSError, LLMError):
                    try:
                        cpath.unlink(missing_ok=True)
                    except OSError:
                        pass

        # json_object 模式：需在提示中显式给出 JSON 结构（DeepSeek/OpenAI 兼容）
        json_hint = "\n\n请严格以 JSON 对象格式输出，不要包含其他内容。"
        if schema:
            json_hint += ("\n输出 JSON 的字段结构必须严格符合以下定义（字段名不可更改，"
                          "缺失的字段填空字符串或空数组）：\n"
                          + json.dumps(schema, ensure_ascii=False)
                          + "\n注意：直接输出数据本身，不要输出 JSON Schema 定义或描述结构的对象。")
        messages = [
            {"role": "system", "content": system + json_hint},
        ]
        if user:
            messages.append({"role": "user", "content": user})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(
                    timeout=self.timeout, follow_redirects=False, trust_env=False
                ) as http:
                    resp = http.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}",
                                 "Content-Type": "application/json"},
                        json=payload,
                    )
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                _record_usage(
                    requests=1,
                    prompt=usage.get("prompt_tokens", 0),
                    completion=usage.get("completion_tokens", 0),
                )
                result = _parse_json_object(content)
                _validate_json_schema(result, schema)
                if self.cache:
                    try:
                        with _CACHE_LOCK:
                            cpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                            temp = cpath.with_suffix(".tmp")
                            temp.write_text(
                                json.dumps(result, ensure_ascii=False), encoding="utf-8"
                            )
                            os.chmod(temp, 0o600)
                            temp.replace(cpath)
                    except OSError:
                        pass  # 缓存写入失败不影响结果
                return result
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
            except (httpx.HTTPError, LLMError) as e:
                last_err = e
                _record_usage(failed=1)
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败: {last_err}")

    def chat_text(self, system: str, user: str, temperature: float = 0.2) -> str:
        """非结构化文本输出。"""
        if self.mode == "mock":
            return self._mock_text(system, user)
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "temperature": temperature}
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(
                    timeout=self.timeout, follow_redirects=False, trust_env=False
                ) as http:
                    resp = http.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}",
                                 "Content-Type": "application/json"},
                        json=payload,
                    )
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                usage = data.get("usage", {})
                _record_usage(
                    requests=1,
                    prompt=usage.get("prompt_tokens", 0),
                    completion=usage.get("completion_tokens", 0),
                )
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, LLMError, KeyError, IndexError, TypeError) as e:
                last_err = e
                _record_usage(failed=1)
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败: {last_err}")

    # ---------- mock 模式（管线验证） ----------
    def _mock_json(self, system: str, user: str, schema: dict | None) -> dict:
        _record_usage(requests=1)
        # 规则卡片 schema：从代码特征确定性构造
        if schema and "self_confidence" in str(schema):
            has_throw = "throw" in user
            has_if = "if" in user
            return {
                "name": "（mock）方法业务逻辑",
                "rule_type": "exception" if has_throw else (
                    "validation" if has_if else "other"),
                "trigger": "方法调用（mock）",
                "pre_condition": "（mock）",
                "logic": ["步骤1（mock）", "步骤2（mock）"],
                "status_change": "（mock）",
                "data_change": "（mock）",
                "follow_up": "（mock）",
                "exception": "（mock）",
                "impact": "（mock）",
                "self_confidence": 60,
                "is_business_rule": has_throw or has_if,
                "notes": "mock 模式生成，仅供管线验证",
            }
        # 业务域 schema
        if schema and "domains" in str(schema):
            mods = re.findall(r"- (\S+)（", user)
            return {"domains": [
                {"name": f"域{i + 1}", "description": "（mock）",
                 "modules": [m]}
                for i, m in enumerate(mods)]}
        # 业务对象 schema
        if schema and "source_tables" in str(schema):
            tables = re.findall(r"^- (\S+)", user, re.M)
            objs = []
            for i, t in enumerate(tables[:30]):
                objs.append({
                    "name": f"业务对象{i + 1}（{t}）",
                    "description": "（mock）",
                    "source_tables": [t],
                    "source_classes": [],
                    "actions": ["创建（mock）", "更新（mock）"],
                })
            return {"objects": objs}
        # 业务流程 schema
        if schema and "business_steps" in str(schema):
            chain = re.findall(r"^- (\S+)  ", user, re.M)[:6]
            return {
                "name": "（mock）流程",
                "description": "（mock）",
                "business_steps": [f"步骤{i + 1}：处理 {c}" for i, c in enumerate(chain)],
            }
        # 业务推演 schema
        if schema and "design_narrative" in str(schema):
            objs = re.findall(r"^- (\S+?)[：:（]", user, re.M)
            rels = [{"from": objs[i], "to": objs[i + 1],
                     "relation": "驱动（mock）", "evidence": "（mock）"}
                    for i in range(len(objs) - 1)]
            return {
                "mainline": objs,
                "relationships": rels,
                "design_narrative": "（mock）业务主线：先生产计划后采购，再销售。",
                "assumptions": ["mock 假设"],
            }
        return _mock_from_schema(schema) if schema else {}

    def _mock_text(self, system: str, user: str) -> str:
        return "（mock）"


def _find_first_list(obj, depth: int = 0) -> list | None:
    """递归查找第一个 list 值（LLM 有时把输出嵌套在 schema 结构里）。"""
    if depth > 6:
        return None
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_first_list(v, depth + 1)
            if found is not None:
                return found
    return None


def _mock_from_schema(schema: dict, field_name: str = ""):
    """Create a deterministic schema-valid value for unsupported mock calls."""
    value_type = schema.get("type")
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if value_type == "object":
        properties = schema.get("properties", {})
        return {
            name: _mock_from_schema(properties[name], name)
            for name in schema.get("required", [])
            if name in properties
        }
    if value_type == "array":
        return []
    if value_type == "string":
        return f"（mock）{field_name or '文本'}"
    if value_type == "integer":
        minimum = schema.get("minimum", 0)
        maximum = schema.get("maximum", 100)
        return max(minimum, min(60, maximum))
    if value_type == "number":
        return schema.get("minimum", 0)
    if value_type == "boolean":
        return False
    return None


def usage_summary() -> str:
    return (f"请求 {USAGE['requests']} 次 | 输入 {USAGE['prompt_tokens']:,} token "
            f"| 输出 {USAGE['completion_tokens']:,} token | 失败 {USAGE['failed']}")
