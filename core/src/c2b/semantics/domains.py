"""业务域识别（S1）+ 可信度计算。"""
import re

from .. import models
from ..llm import LLMClient, _find_first_list

DOMAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "modules": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "description", "modules"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["domains"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是遗留系统业务分析师。根据给定的模块清单（模块名 + 代表性类名 + 注释），"
    "把模块归类为若干业务域。约束：1) 只依据给定信息，不确定的模块归入'未归类'；"
    "2) 输出中文业务域名称；3) 每个模块只能属于一个域。"
)


def recognize_domains(client: LLMClient, modules: list[models.Module],
                      classes: dict[int, list[str]], batch: int = 45) -> dict:
    """LLM 聚类模块 → 业务域（分批识别防止长清单导致模型退化）。"""
    all_domains: list[dict] = []
    for i in range(0, len(modules), batch):
        chunk = modules[i:i + batch]
        lines = []
        for m in chunk:
            reps = classes.get(m.id, [])[:3]
            lines.append(f"- {m.name}（{m.lines:,} 行）代表性类: "
                         f"{', '.join(reps) or '-'}")
        user = "## 模块清单\n" + "\n".join(lines)
        result = client.chat_json(SYSTEM_PROMPT, user, schema=DOMAIN_SCHEMA)
        # 宽容解析：LLM 可能用不同 key 或嵌套结构
        domains = (result.get("domains") or result.get("business_domains")
                   or result.get("domain_list")
                   or _find_first_list(result) or [])
        if not isinstance(domains, list) or not domains:
            raise ValueError(f"业务域输出格式异常: {str(result)[:200]}")
        all_domains.extend(domains)
    return {"domains": all_domains}


def compute_confidence(card: dict, cands: list[dict], entry_kind: str | None) -> int:
    """可信度 = 基础 50 + 静态证据 + LLM 自评，clamp 到 0-100。"""
    score = 50
    kinds = {c["kind"] for c in cands}
    if "throw" in kinds:
        score += 10
    if "if" in kinds:
        score += 10
    if "calc" in kinds:
        score += 10
    if "status_assign" in kinds:
        score += 5
    if entry_kind:
        score += 5  # 入口方法调用链更完整
    if len(cands) >= 3:
        score += 5
    # LLM 自评调和（取中间值）
    self_c = card.get("self_confidence") or 50
    score = (score + self_c) // 2
    return max(0, min(100, score))
