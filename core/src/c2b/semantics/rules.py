"""规则候选提取与规则卡片生成（S3）。

规则候选：确定性层从 AST 提取 if/throw/状态赋值/运算特征；
卡片生成：LLM 基于"方法签名 + 代码片段 + 特征"产出结构化规则卡片。
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_java

from .. import models
from ..llm import LLMClient
from ..scanner.probe import detect_encoding

_LANG = Language(tree_sitter_java.language())
_PARSER = Parser(_LANG)

CALC_HINT = re.compile(r"(amount|price|qty|num|total|count|rate|money|金额|数量|单价)", re.I)
STATUS_HINT = re.compile(r"(status|state|flag|phase|phase_cd|sts)", re.I)

RULE_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "rule_type": {"type": "string",
                      "enum": ["validation", "calculation", "status",
                               "permission", "trigger", "exception", "other"]},
        "trigger": {"type": "string"},
        "pre_condition": {"type": "string"},
        "logic": {"type": "array", "items": {"type": "string"}},
        "status_change": {"type": "string"},
        "data_change": {"type": "string"},
        "follow_up": {"type": "string"},
        "exception": {"type": "string"},
        "impact": {"type": "string"},
        "self_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "is_business_rule": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["name", "rule_type", "trigger", "pre_condition", "logic",
                 "status_change", "data_change", "follow_up", "exception",
                 "impact", "self_confidence", "is_business_rule", "notes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是遗留系统业务分析师。你的任务是从 Java 代码片段中还原真实的业务规则。"
    "约束：1) 只基于给定的代码片段与特征，禁止臆测未出现的行为；"
    "2) 业务含义不确定时，在 notes 中注明'疑似'；3) 代码只是技术实现而非业务规则时，"
    "is_business_rule 设为 false；4) 所有输出使用中文（术语可用日文/英文原文）。"
)


def extract_candidates(abs_path: Path, method: dict) -> list[dict]:
    """从方法体内提取规则特征（确定性层，不调用 LLM）。"""
    try:
        data = abs_path.read_bytes()
    except OSError:
        return []
    tree = _PARSER.parse(data)
    root = tree.root_node
    start, end = method["start_line"], method["end_line"]
    target = None

    # 定位方法节点
    def find(node):
        nonlocal target
        if target:
            return
        if node.type in ("method_declaration", "constructor_declaration"):
            s = node.start_point[0] + 1
            if s == start and node.end_point[0] + 1 == end:
                target = node
                return
        for c in node.named_children:
            find(c)

    find(root)
    if target is None:
        return []

    candidates: list[dict] = []
    for node in target.named_children:
        _walk_features(node, candidates)
    return candidates


def _walk_features(node, out: list[dict]):
    t = node.type
    if t == "if_statement":
        cond = node.child_by_field_name("condition")
        if cond is not None:
            out.append({"kind": "if", "line": node.start_point[0] + 1,
                        "text": _snip(cond.text)})
    elif t == "throw_statement":
        txt = _snip(node.text)
        # 提取异常消息字符串
        m = re.search(r"[\"']([^\"']{4,200})[\"']", txt)
        out.append({"kind": "throw", "line": node.start_point[0] + 1,
                    "text": txt, "message": m.group(1) if m else None})
    elif t == "assignment_expression" or (t == "local_variable_declaration"):
        txt = _snip(node.text, 200)
        if STATUS_HINT.search(txt):
            out.append({"kind": "status_assign", "line": node.start_point[0] + 1,
                        "text": txt})
        elif CALC_HINT.search(txt):
            out.append({"kind": "calc", "line": node.start_point[0] + 1,
                        "text": txt})
    elif t == "binary_expression":
        txt = _snip(node.text, 200)
        if CALC_HINT.search(txt):
            out.append({"kind": "calc", "line": node.start_point[0] + 1,
                        "text": txt})

    for c in node.named_children:
        _walk_features(c, out)


def _snip(b: bytes, limit: int = 300) -> str:
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def read_snippet(abs_path: Path, start: int, end: int, limit: int = 120) -> str:
    """读方法体代码片段（转码处理）。"""
    try:
        data = abs_path.read_bytes()
    except OSError:
        return ""
    lines = data.splitlines()
    seg = lines[start - 1:end]
    if len(seg) > limit:
        seg = seg[:limit] + [f"... (共 {end - start + 1} 行，截断)".encode("utf-8")]
    enc = detect_encoding(b"\n".join(seg))
    try:
        return "\n".join(x.decode(enc, errors="replace") for x in seg)
    except LookupError:
        return "\n".join(x.decode("utf-8", errors="replace") for x in seg)


def build_prompt(method: models.CodeObject, snippet: str,
                 candidates: list[dict]) -> str:
    feats = "\n".join(
        f"- [{c['line']}] {c['kind']}: {c.get('message') or c['text']}"
        for c in candidates[:15])
    return (
        f"## 方法\n"
        f"类: {method.fq_name}\n"
        f"签名: {method.signature or ''}\n"
        f"注释: {method.comment or ''}\n"
        f"文件: {method.file_path} 行 {method.start_line}-{method.end_line}\n"
        f"入口类型: {method.entry_kind or '内部方法'}\n\n"
        f"## 提取到的规则特征（代码行证据）\n{feats or '（无显著特征）'}\n\n"
        f"## 代码片段\n```java\n{snippet}\n```\n\n"
        f"请输出该方法的业务规则卡片 JSON。"
    )


def generate_card(client: LLMClient, method: models.CodeObject,
                  snippet: str, candidates: list[dict],
                  data_tables: list[str]) -> dict:
    """LLM 生成规则卡片；失败抛异常由调用方处理。"""
    user = build_prompt(method, snippet, candidates)
    if data_tables:
        user += f"\n\n相关数据表（SQL 证据）: {', '.join(data_tables[:10])}"
    return client.chat_json(SYSTEM_PROMPT, user, schema=RULE_CARD_SCHEMA)


def label_methods(client: LLMClient, methods: list[models.CodeObject],
                  root: Path, tables_by_method: dict[int, list[str]] | None = None,
                  concurrency: int = 8,
                  progress_cb=None) -> list[tuple[models.CodeObject, dict | None, str | None]]:
    """并发标注方法列表。返回 [(method, card, error)]。
    progress_cb(done, total)：每完成一个方法回调。"""
    tables_by_method = tables_by_method or {}
    results: list[tuple[models.CodeObject, dict | None, str | None]] = []
    total = len(methods)
    done = 0
    progress_lock = threading.Lock()

    def work(m: models.CodeObject):
        nonlocal done
        try:
            path = root / m.file_path
            snippet = read_snippet(path, m.start_line, m.end_line)
            cands = (extract_candidates(path, {"start_line": m.start_line,
                                               "end_line": m.end_line})
                     if m.file_path.endswith(".java") else [])
            card = generate_card(client, m, snippet, cands,
                                 tables_by_method.get(m.id, []))
            return m, card, None
        except Exception as e:
            return m, None, str(e)
        finally:
            # Keep progress monotonic.  Concurrent callbacks can otherwise
            # overwrite a newer database value with an older completion count.
            with progress_lock:
                done += 1
                if progress_cb:
                    progress_cb(done, total)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for m, card, err in ex.map(work, methods):
            results.append((m, card, err))
    return results
