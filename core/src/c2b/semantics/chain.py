"""S6 业务推演：基于业务对象与流程，还原业务驱动关系与产品设计主线。

确定性证据收集：
  - 对象→表集合；扫描 SQL 文本找表间 JOIN/公共字段关联
  - 对象来源类之间的调用边（call_edges）
  - 对象相关流程的入口与业务步骤
LLM 推演：
  - 对象间驱动关系（谁驱动谁、先后顺序、因果关系）
  - 业务主线（价值链）：如 生产计划 → 采购 → 入库 → 销售 → 收款
  - 产品设计逻辑叙事 + 推断假设
"""
import re
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient

CHAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "mainline": {"type": "array", "items": {"type": "string"}},
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "relation": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["from", "to", "relation", "evidence"],
                "additionalProperties": False,
            },
        },
        "design_narrative": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mainline", "relationships", "design_narrative",
                 "assumptions"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是资深企业 ERP 产品设计师。根据遗留系统中识别出的业务对象、业务流程及其"
    "关联证据，还原该系统当初的产品设计逻辑。约束：1) 只依据给定证据推演，"
    "证据不足的推断必须写入 assumptions；2) relationships 描述对象间驱动关系"
    "（from 驱动/影响 to），relation 用中文（如：依据/触发/产出/消耗/关联）；"
    "3) mainline 给出业务主线（从业务起点到终点的对象序列）；4) design_narrative"
    "解释整体设计逻辑（如：计划驱动型/订单驱动型），100-300 字；5) 全部中文；"
    "6) 每条 relationship 的 evidence 必须引用给定的关联证据（表/流程/调用），"
    "没有任何证据支撑的关系必须写入 assumptions 而不是 relationships。"
)


def _table_pairs_from_sql(session: Session, scan_id: int) -> list[tuple[str, str]]:
    """从 SQL 文本提取表间关联（JOIN 或同语句多表）。"""
    pairs = set()
    rows = session.execute(
        select(models.SqlOperation.sql_text)
        .where(models.SqlOperation.scan_id == scan_id,
               models.SqlOperation.sql_text.isnot(None))).scalars().all()
    for sql in rows[:2000]:
        tables = re.findall(
            r"\b(?:from|join|into|update)\s+([A-Za-z_][A-Za-z0-9_$]*)",
            sql, re.IGNORECASE)
        tables = [t.lower() for t in tables]
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                pairs.add((tables[i], tables[j]))
    return sorted(pairs)


def _object_evidence(session: Session, scan_id: int,
                     objs: list[models.BusinessObject]) -> dict:
    """收集每个对象的证据：表集合 + 相关流程 + 类间调用。"""
    evidence = {}
    for o in objs:
        tables = {t.lower() for t in (o.source_tables or [])}
        flows = []
        for f in session.execute(
                select(models.BusinessFlow)
                .where(models.BusinessFlow.scan_id == scan_id)).scalars().all():
            steps = " ".join(f.business_steps or [])
            if any(t in steps.lower() for t in tables) or any(
                    t in (f.entry_name or "") for t in tables):
                flows.append(f.name or f.entry_name)
        evidence[o.name] = {
            "tables": sorted(tables),
            "flows": flows[:5],
        }
    return evidence


def _method_to_object(session: Session, scan_id: int,
                      objs: list[models.BusinessObject]) -> dict[int, str]:
    """方法 → 业务对象：按方法操作的 SQL 表归属（表属于哪个对象）。

    方法可能操作多个对象的表 → 归属操作表最多的对象。
    """
    obj_tables = {o.name: {t.lower() for t in (o.source_tables or [])}
                  for o in objs}
    # 表 → 对象
    table_obj: dict[str, str] = {}
    for name, tables in obj_tables.items():
        for t in tables:
            table_obj[t] = name
    # 方法 → 操作的表（含读写）
    method_tables: dict[int, set] = {}
    for mid, tbl, _op in session.execute(
            select(models.SqlOperation.host_obj_id,
                   models.SqlOperation.table_name,
                   models.SqlOperation.op)
            .where(models.SqlOperation.scan_id == scan_id,
                   models.SqlOperation.host_obj_id.isnot(None),
                   models.SqlOperation.table_name.isnot(None))).all():
        method_tables.setdefault(mid, set()).add((tbl or "").lower())
    # 方法 → 对象（计数归属）
    method_obj: dict[int, str] = {}
    for mid, tables in method_tables.items():
        counts: dict[str, int] = {}
        for t in tables:
            o = table_obj.get(t)
            if o:
                counts[o] = counts.get(o, 0) + 1
        if counts:
            method_obj[mid] = max(counts, key=counts.get)
    return method_obj


def _dataflow_edges(session: Session, scan_id: int,
                    method_obj: dict[int, str],
                    obj_tables: dict[str, set]) -> dict[tuple[str, str], int]:
    """数据流有向证据：写表方法（A 对象）→ 读表方法（B 对象）= A 产出 B 消费。

    对每张表：写方对象 → 读方对象，加 1。
    """
    # 表 → (写方法集合, 读方法集合)
    table_writers: dict[str, set] = {}
    table_readers: dict[str, set] = {}
    for mid, tbl, op in session.execute(
            select(models.SqlOperation.host_obj_id,
                   models.SqlOperation.table_name,
                   models.SqlOperation.op)
            .where(models.SqlOperation.scan_id == scan_id,
                   models.SqlOperation.host_obj_id.isnot(None),
                   models.SqlOperation.table_name.isnot(None))).all():
        t = (tbl or "").lower()
        if op == models.SqlOp.SELECT:
            table_readers.setdefault(t, set()).add(mid)
        else:
            table_writers.setdefault(t, set()).add(mid)
    edges: dict[tuple[str, str], int] = {}
    for t, writers in table_writers.items():
        for w in writers:
            wo = method_obj.get(w)
            if not wo:
                continue
            for r in table_readers.get(t, set()):
                ro = method_obj.get(r)
                if ro and ro != wo:
                    key = (wo, ro)
                    edges[key] = edges.get(key, 0) + 1
    return edges


def _call_edges(session: Session, scan_id: int,
                method_obj: dict[int, str]) -> dict[tuple[str, str], int]:
    """对象间调用有向证据：call_edges 方法→方法 聚合为 对象→对象。"""
    rows = session.execute(
        select(models.CallEdge.from_obj_id, models.CallEdge.to_obj_id)
        .where(models.CallEdge.scan_id == scan_id,
               models.CallEdge.from_obj_id.isnot(None),
               models.CallEdge.to_obj_id.isnot(None))).all()
    edges: dict[tuple[str, str], int] = {}
    for f, t in rows:
        fo, to = method_obj.get(f), method_obj.get(t)
        if fo and to and fo != to:
            key = (fo, to)
            edges[key] = edges.get(key, 0) + 1
    return edges


def _link_evidence(session: Session, scan_id: int,
                   objs: list[models.BusinessObject]) -> dict[tuple[str, str], int]:
    """对象间关联证据强度（确定性层，不依赖 LLM）：
      +2 表间 JOIN/同语句出现；+2 跨对象表字段同名交集（外键线索）；
      +1 共享表；+1 共享流程。返回 {(对象A, 对象B): 证据分}。"""
    obj_tables = {o.name: {t.lower() for t in (o.source_tables or [])}
                  for o in objs}
    names = [o.name for o in objs]
    score: dict[tuple[str, str], int] = {}

    def bump(a: str, b: str, v: int):
        key = (a, b) if a <= b else (b, a)
        score[key] = score.get(key, 0) + v

    # 表间 JOIN/同语句（SQL 文本）
    sql_rows = session.execute(
        select(models.SqlOperation.sql_text)
        .where(models.SqlOperation.scan_id == scan_id,
               models.SqlOperation.sql_text.isnot(None))).scalars().all()
    for sql in sql_rows[:2000]:
        ts = {t.lower() for t in re.findall(
            r"\b(?:from|join|into|update)\s+([A-Za-z_][A-Za-z0-9_$]*)",
            sql, re.IGNORECASE)}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if (obj_tables[names[i]] & ts) and (obj_tables[names[j]] & ts):
                    bump(names[i], names[j], 2)

    # 字段级关联（外键线索）：SQL 操作涉及字段的跨表同名交集
    fields_by_table: dict[str, set] = {}
    for tbl, fields in session.execute(
            select(models.SqlOperation.table_name, models.SqlOperation.fields)
            .where(models.SqlOperation.scan_id == scan_id,
                   models.SqlOperation.fields.isnot(None))).all():
        if not fields:
            continue
        fields_by_table.setdefault((tbl or "").lower(), set()).update(fields)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            fset_i = set().union(*(fields_by_table.get(t, set())
                                   for t in obj_tables[names[i]])) if obj_tables[names[i]] else set()
            fset_j = set().union(*(fields_by_table.get(t, set())
                                   for t in obj_tables[names[j]])) if obj_tables[names[j]] else set()
            common = fset_i & fset_j
            # 忽略通用字段名，取疑似业务键（含 id/no/cd/date/status 的字段）
            biz_keys = {c for c in common
                        if re.search(r"(id|no|cd|date|status|flag|type)$", c,
                                     re.IGNORECASE) or
                        re.search(r"(amount|qty|price|money|num)", c, re.IGNORECASE)}
            if biz_keys:
                bump(names[i], names[j], 2)

    # 共享表
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if obj_tables[names[i]] & obj_tables[names[j]]:
                bump(names[i], names[j], 1)

    # 共享流程（同一流程步骤涉及两对象的数据表）
    flow_obj: dict[int, list[str]] = {}
    for f in session.execute(
            select(models.BusinessFlow)
            .where(models.BusinessFlow.scan_id == scan_id)).scalars().all():
        steps = " ".join(f.business_steps or [])
        for o in objs:
            if any(t in steps.lower() for t in obj_tables[o.name]):
                flow_obj.setdefault(f.id, []).append(o.name)
    for ons in flow_obj.values():
        for i in range(len(ons)):
            for j in range(i + 1, len(ons)):
                bump(ons[i], ons[j], 1)

    return dict(score)


def _verify_level(score: int) -> tuple[str, str]:
    """证据分 → 衡木式核实等级（对数分档，避免大数值饱和）：
    ≥50 V4 多源强证据 / 10-49 V3 强证据 / 3-9 V2 部分证据 /
    1-2 V1 单一弱证据 / 0 V0 仅推断。"""
    if score >= 50:
        return "V4", "多源强证据"
    if score >= 10:
        return "V3", "强证据"
    if score >= 3:
        return "V2", "部分证据"
    if score >= 1:
        return "V1", "单一弱证据"
    return "V0", "仅推断"


def build_prompt(objs: list[models.BusinessObject], table_pairs: list[tuple],
                 evidence: dict, directed: dict) -> str:
    lines = ["## 业务对象清单"]
    for o in objs:
        lines.append(
            f"- {o.name}：{o.description or ''}｜动作: {', '.join(o.actions or [])[:80]}")
    lines.append("\n## 对象间有向证据（数据流/调用，方向即驱动方向）")
    if directed:
        for (a, b), n in sorted(directed.items(), key=lambda x: -x[1])[:60]:
            lines.append(f"- {a} → {b}（{n} 次证据：产出/调用）")
    else:
        lines.append("（无有向证据）")
    lines.append("\n## 对象关联证据（无方向）")
    for name, ev in evidence.items():
        lines.append(f"- {name}：表 {ev['tables'][:8]}｜相关流程 {ev['flows'] or '无'}")
    lines.append("\n## 代码中出现的表间关联（JOIN/同语句）")
    for a, b in table_pairs[:40]:
        lines.append(f"- {a} ↔ {b}")
    lines.append("\n请推演业务对象间的驱动关系与业务主线：优先依据有向证据的方向，"
                 "有向证据的关系写入 relationships（evidence 引用证据），"
                 "无证据的关系写入 assumptions。输出 JSON。")
    return "\n".join(lines)


def run_chain(s: Session, scan_id: int, client: LLMClient) -> dict | None:
    """执行 S6 推演并落库，返回推演结果 dict。"""
    objs = list(s.execute(
        select(models.BusinessObject)
        .where(models.BusinessObject.scan_id == scan_id)
        .order_by(models.BusinessObject.id)).scalars().all())
    if len(objs) < 2:
        return None
    pairs = _table_pairs_from_sql(s, scan_id)
    evidence = _object_evidence(s, scan_id, objs)
    # 有向证据：数据流（写→读）+ 调用链（方法级聚合）
    method_obj = _method_to_object(s, scan_id, objs)
    directed: dict[tuple[str, str], int] = {}
    for k, v in _dataflow_edges(s, scan_id, method_obj,
                                {o.name: {t.lower() for t in (o.source_tables or [])}
                                 for o in objs}).items():
        directed[k] = directed.get(k, 0) + v * 2   # 数据流权重 2
    for k, v in _call_edges(s, scan_id, method_obj).items():
        directed[k] = directed.get(k, 0) + v       # 调用权重 1
    link_scores = _link_evidence(s, scan_id, objs)
    result = client.chat_json(
        SYSTEM_PROMPT,
        build_prompt(objs, pairs, evidence, directed),
        schema=CHAIN_SCHEMA)
    mainline = (result.get("mainline") or result.get("chain")
                or _find_list(result, ["mainline", "chain"]))
    rels = (result.get("relationships") or result.get("relations")
            or _find_list(result, ["relationships", "relations"]))
    if not isinstance(mainline, list) or not isinstance(rels, list):
        raise ValueError(f"业务推演输出格式异常: {str(result)[:300]}")

    allowed_names = {obj.name for obj in objs}
    raw_mainline = [str(item) for item in mainline if str(item).strip()]
    mainline = list(dict.fromkeys(
        item for item in raw_mainline if item in allowed_names
    ))
    assumptions = list(result.get("assumptions") or _find_list(
        result, ["assumptions"]) or [])
    for unknown in dict.fromkeys(
        item for item in raw_mainline if item not in allowed_names
    ):
        assumptions.append(
            f"主线中的对象「{unknown}」不在已识别业务对象清单中，已移除"
        )

    # 衡木理念：模型建议 + 确定性证据决定可信。V0 只能进入
    # assumptions，不得作为已证实关系或图上边展示。
    # 核实等级：优先看有向证据（数据流/调用），再叠加无向关联证据
    annotated = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        a, b = str(r.get("from", "")), str(r.get("to", ""))
        if a not in allowed_names or b not in allowed_names or a == b:
            assumptions.append(
                f"关系「{a or '?'} → {b or '?'}」的端点不属于已识别业务对象，已移除"
            )
            continue
        dscore = directed.get((a, b), 0)
        rscore = directed.get((b, a), 0)
        lscore = link_scores.get((a, b), link_scores.get((b, a), 0))
        # 总分：有向证据（方向匹配）+ 无向关联证据
        score = dscore * 2 + lscore
        # 若方向相反（B→A 证据强于 A→B），提示方向存疑
        reverse_hint = ""
        if rscore > dscore * 2:
            reverse_hint = "（证据方向存疑：反向证据更强）"
            score = max(score, 1)
        level, label = _verify_level(score)
        annotated.append({**r, "verification": level,
                          "verification_label": label,
                          "evidence_score": score,
                          "dataflow_score": dscore,
                          "reverse_hint": reverse_hint})

    no_evidence = [r for r in annotated if r["verification"] == "V0"]
    evidenced = [r for r in annotated if r["verification"] != "V0"]
    for r in no_evidence:
        assumptions.append(
            f"关系「{r.get('from')} → {r.get('to')}（{r.get('relation')}）」"
            f"无确定性证据支撑，已从关系图移除，需人工确认")

    evidenced_pairs = {(r["from"], r["to"]) for r in evidenced}
    for source, target in zip(mainline, mainline[1:]):
        if (source, target) not in evidenced_pairs:
            assumptions.append(
                f"主线顺序「{source} → {target}」缺少同向确定性证据，需人工确认"
            )

    assumptions = list(dict.fromkeys(str(item) for item in assumptions if item))
    confidence_by_level = {"V1": 50, "V2": 70, "V3": 85, "V4": 95}
    confidence = (
        round(sum(confidence_by_level[r["verification"]] for r in evidenced)
              / len(evidenced))
        if evidenced else 30
    )

    chain = models.BusinessChain(
        scan_id=scan_id, mainline=mainline, relationships=evidenced,
        design_narrative=result.get("design_narrative") or result.get("narrative"),
        assumptions=assumptions, confidence=confidence, model=client.model)
    s.add(chain)
    return chain


def _find_list(obj, keys: list[str], depth: int = 0):
    if depth > 6:
        return None
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, list):
                return v
            found = _find_list(v, keys, depth + 1)
            if found is not None:
                return found
    return None
