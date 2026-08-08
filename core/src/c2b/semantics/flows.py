"""S4 流程归纳（简化版）：入口方法 + 调用链 → 业务动作序列。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient

FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "business_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "description", "business_steps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是遗留系统业务分析师。根据程序入口及其调用链，还原该程序执行的业务流程。"
    "约束：1) business_steps 用中文业务动作描述（如：读取待处理数据→逐条校验→更新状态），"
    "2-6 步；2) 只依据调用链中方法名推断，不确定写'疑似'；3) 禁止编造代码中不存在的行为。"
)


def pick_entries(s: Session, scan_id: int, limit: int = 20) -> list[models.CodeObject]:
    q = (select(models.CodeObject)
         .where(models.CodeObject.scan_id == scan_id,
                models.CodeObject.obj_type == models.ObjType.METHOD,
                models.CodeObject.is_entry.is_(True))
         .order_by(models.CodeObject.entry_kind))
    return list(s.execute(q.limit(limit)).scalars().all())


def call_chain(s: Session, scan_id: int, method_id: int, depth: int = 2,
               max_nodes: int = 15) -> list[models.CodeObject]:
    """BFS 调用链（从入口出发，取前 max_nodes 个被调方法）。"""
    nodes = []
    seen = {method_id}
    frontier = [method_id]
    for _ in range(depth):
        if not frontier or len(nodes) >= max_nodes:
            break
        next_f = []
        for mid in frontier:
            rows = s.execute(
                select(models.CallEdge.to_obj_id)
                .where(models.CallEdge.scan_id == scan_id,
                       models.CallEdge.from_obj_id == mid,
                       models.CallEdge.to_obj_id.isnot(None))).scalars().all()
            for toid in rows:
                if toid not in seen and len(nodes) < max_nodes:
                    seen.add(toid)
                    obj = s.get(models.CodeObject, toid)
                    if obj:
                        nodes.append(obj)
                        next_f.append(toid)
        frontier = next_f
    return nodes


def build_prompt(entry: models.CodeObject, chain: list[models.CodeObject]) -> str:
    lines = [
        f"## 程序入口\n{entry.file_path}:{entry.start_line}  {entry.name}"
        f"（{entry.entry_kind}）\n签名: {entry.signature or ''}\n",
        "## 调用链方法（按调用顺序）",
    ]
    for c in chain[:15]:
        lines.append(f"- {c.name}  {c.file_path.split('/')[-1]}:{c.start_line}"
                     f"{'  ' + (c.comment or '')[:60] if c.comment else ''}")
    return "\n".join(lines)


def recognize(client: LLMClient, entry: models.CodeObject,
              chain: list[models.CodeObject]) -> dict:
    result = client.chat_json(SYSTEM_PROMPT, build_prompt(entry, chain),
                              schema=FLOW_SCHEMA)
    steps = (result.get("business_steps") or result.get("steps")
             or result.get("flow") or [])
    name = (result.get("name") or result.get("flow_name") or "").strip()
    return {"name": name,
            "description": result.get("description") or "",
            "business_steps": steps if isinstance(steps, list) else []}


def run_flows(s: Session, scan_id: int, client: LLMClient,
              limit: int = 10) -> int:
    """执行 S4 并落库，返回流程数。"""
    entries = pick_entries(s, scan_id, limit=limit)
    n = 0
    for e in entries:
        chain = call_chain(s, scan_id, e.id)
        if not chain:
            continue
        try:
            f = recognize(client, e, chain)
        except Exception:
            continue
        steps = f.get("business_steps") or []
        if not steps:
            continue
        s.add(models.BusinessFlow(
            scan_id=scan_id, name=f.get("name") or e.name,
            description=f.get("description"),
            entry_method_id=e.id, entry_name=f"{e.file_path}:{e.start_line}",
            business_steps=steps,
            tech_steps=[c.name for c in chain[:15]],
            confidence=60))
        n += 1
    return n
