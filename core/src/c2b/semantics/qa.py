"""AI 问答（M5b）：图谱检索 + LLM 生成带证据回答（PRD 7.3 / 10.10）。

回答结构（11 段）：结论/触发条件/处理步骤/状态变化/数据变化/后续动作/
异常分支/影响范围/证据/可信度/待确认项。
原则：只基于图谱中已分析的内容回答，未覆盖必须显式标注。
"""
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient

QA_SCHEMA = {
    "type": "object",
    "properties": {
        "conclusion": {"type": "string"},
        "trigger": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "status_change": {"type": "string"},
        "data_change": {"type": "string"},
        "follow_up": {"type": "string"},
        "exceptions": {"type": "array", "items": {"type": "string"}},
        "impact": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "uncovered": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["conclusion", "trigger", "steps", "status_change",
                 "data_change", "follow_up", "exceptions", "impact",
                 "evidence", "confidence", "uncovered"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是遗留系统业务分析师，基于给定的业务知识图谱回答系统业务问题。"
    "约束：1) 只能依据给定图谱内容回答，图谱未覆盖的内容必须写入 uncovered；"
    "2) 每条结论必须在 evidence 中引用图谱来源（规则/流程/表/文件）；"
    "3) 不确定的内容标'疑似'；4) 全部中文。"
)


def _search(s: Session, scan_id: int, q: str) -> dict:
    """图谱检索：匹配业务对象/规则/流程/表/入口。"""
    tokens = [t for t in re.split(r"[，。？、\s]|吗|呢|是|的|在|会|怎么|如何", q)
              if len(t) >= 2]

    def pattern(token: str) -> str:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def text_match(*columns):
        return or_(*(
            column.ilike(pattern(token), escape="\\")
            for column in columns for token in tokens
        ))

    def match(name: str) -> bool:
        name_l = name.lower()
        return any(t.lower() in name_l for t in tokens) or any(
            t in name_l for t in tokens)

    if not tokens:
        objects = rules = flows = tables = []
    else:
        objects = list(s.execute(
            select(models.BusinessObject).where(
                models.BusinessObject.scan_id == scan_id,
                text_match(models.BusinessObject.name,
                           models.BusinessObject.description),
            ).limit(5)
        ).scalars().all())
        rules = list(s.execute(
            select(models.BusinessRule).where(
                models.BusinessRule.scan_id == scan_id,
                models.BusinessRule.state != models.RuleState.DISCARDED,
                text_match(models.BusinessRule.name, models.BusinessRule.module,
                           models.BusinessRule.logic),
            ).order_by(models.BusinessRule.confidence.desc()).limit(8)
        ).scalars().all())
        named_flows = list(s.execute(
            select(models.BusinessFlow).where(
                models.BusinessFlow.scan_id == scan_id,
                text_match(models.BusinessFlow.name, models.BusinessFlow.description,
                           models.BusinessFlow.entry_name),
            ).limit(5)
        ).scalars().all())
        flows = named_flows
        if len(flows) < 5:
            seen = {flow.id for flow in flows}
            # JSON array text search is dialect-specific.  Bound the fallback
            # candidate set instead of loading the entire project into Python.
            candidates = s.execute(
                select(models.BusinessFlow).where(
                    models.BusinessFlow.scan_id == scan_id,
                    models.BusinessFlow.id.notin_(seen) if seen else True,
                ).order_by(models.BusinessFlow.id.desc()).limit(100)
            ).scalars().all()
            flows.extend(
                flow for flow in candidates
                if any(match(step) for step in (flow.business_steps or []))
            )
            flows = flows[:5]
        tables = list(s.execute(
            select(models.DataTable).where(
                models.DataTable.scan_id == scan_id,
                text_match(models.DataTable.name, models.DataTable.comment),
            ).limit(10)
        ).scalars().all())
    chain = s.execute(
        select(models.BusinessChain)
        .where(models.BusinessChain.scan_id == scan_id)
        .order_by(models.BusinessChain.id.desc()).limit(1)).scalar_one_or_none()
    return {"objects": objects, "rules": rules, "flows": flows,
            "tables": tables, "chain": chain}


def _build_context(hit: dict) -> str:
    parts = []
    if hit["objects"]:
        parts.append("## 相关业务对象")
        for o in hit["objects"]:
            parts.append(f"- {o.name}：{o.description or '-'}｜动作: "
                         f"{', '.join(o.actions or [])[:100]}")
    if hit["rules"]:
        parts.append("## 相关规则卡片")
        for r in hit["rules"]:
            parts.append(
                f"- {r.name or '(未命名)'} [{r.confidence}%]："
                f"触发 {r.trigger or '-'}；逻辑 {(r.logic or '-')[:80]}；"
                f"数据 {(r.data_change or '-')[:60]}；"
                f"证据 {r.evidence_file}:{r.evidence_lines}")
    if hit["flows"]:
        parts.append("## 相关业务流程")
        for f in hit["flows"]:
            parts.append(f"- {f.name or f.entry_name}："
                         f"{' -> '.join((f.business_steps or [])[:8])}")
    if hit["tables"]:
        parts.append("## 相关数据表")
        for t in hit["tables"]:
            parts.append(f"- {t.name}")
    if hit["chain"] and hit["chain"].mainline:
        parts.append("## 业务主线")
        parts.append(" -> ".join(hit["chain"].mainline))
        parts.append(hit["chain"].design_narrative or "")
    if not parts:
        parts.append("（图谱中未检索到与问题相关的业务对象/规则/流程/表）")
    return "\n".join(parts)


def answer(s: Session, scan_id: int, question: str,
           client: LLMClient) -> dict:
    """问答主入口：检索 + LLM 生成。返回 11 段结构 dict。"""
    hit = _search(s, scan_id, question)
    ctx = _build_context(hit)
    user = f"## 用户问题\n{question}\n\n## 业务知识图谱内容\n{ctx[:8000]}"
    result = client.chat_json(SYSTEM_PROMPT, user, schema=QA_SCHEMA)
    # 宽容解析
    conclusion = (result.get("conclusion") or result.get("answer")
                  or result.get("结论") or "")
    if not conclusion:
        raise ValueError(f"问答输出格式异常: {str(result)[:200]}")
    return {
        "question": question,
        "conclusion": conclusion,
        "trigger": result.get("trigger") or result.get("trigger_condition") or "",
        "steps": result.get("steps") or result.get("处理步骤") or [],
        "status_change": result.get("status_change") or "",
        "data_change": result.get("data_change") or "",
        "follow_up": result.get("follow_up") or "",
        "exceptions": result.get("exceptions") or result.get("exception") or [],
        "impact": result.get("impact") or "",
        "evidence": result.get("evidence") or [],
        "confidence": result.get("confidence") or 0,
        "uncovered": result.get("uncovered") or [],
        "hit": {"objects": [o.name for o in hit["objects"]],
                "rules": [r.name for r in hit["rules"]],
                "flows": [f.name for f in hit["flows"]],
                "tables": [t.name for t in hit["tables"]]},
    }
