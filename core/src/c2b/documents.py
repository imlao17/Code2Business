"""文档生成（M5a）：从已确认业务知识渲染正式交付文档。

模板：Jinja2 内嵌定义（MVP 无外部模板文件，保持自包含）。
导出：Markdown（默认）+ Word（python-docx 段落化）。
原则：正式文档只纳入人工确认的业务域、对象、流程及已确认/人工修改
的规则；尚无确认状态的 AI 主线也只进入草稿。已废弃规则始终排除。
"""
import re
from io import BytesIO

from docx import Document as DocxDocument
from docx.shared import Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

DOC_TYPES = {
    "overview": ("系统现状说明书", "系统规模、模块地图、技术栈、入口、表与 SQL 概览"),
    "functions": ("功能清单", "业务域 → 业务对象 → 业务动作 的功能层级清单"),
    "rules": ("业务规则清单", "已确认规则卡片（草稿模式可包含 AI 未确认项）"),
    "dictionary": ("数据字典", "数据表、字段线索、SQL 读写场景"),
    "apis": ("接口清单", "API/批处理入口清单（MVP 以批处理入口为主）"),
    "acceptance": ("验收清单", "系统功能验收项（基于业务对象与动作生成）"),
}


def _gather(s: Session, scan_id: int, include_unconfirmed: bool = False) -> dict:
    """收集文档所需数据。"""
    scan = s.get(models.Scan, scan_id)
    project = s.get(models.Project, scan.project_id) if scan else None
    meta = scan.meta or {}
    stats = scan.stats or {}

    modules = list(s.execute(
        select(models.Module).where(models.Module.scan_id == scan_id)
        .order_by(models.Module.lines.desc())).scalars().all())
    domain_query = select(models.BusinessDomain).where(
        models.BusinessDomain.scan_id == scan_id
    )
    object_query = select(models.BusinessObject).where(
        models.BusinessObject.scan_id == scan_id
    )
    flow_query = select(models.BusinessFlow).where(
        models.BusinessFlow.scan_id == scan_id
    )
    if not include_unconfirmed:
        domain_query = domain_query.where(models.BusinessDomain.confirmed.is_(True))
        object_query = object_query.where(models.BusinessObject.confirmed.is_(True))
        flow_query = flow_query.where(models.BusinessFlow.confirmed.is_(True))
    domains = list(s.execute(
        domain_query.order_by(models.BusinessDomain.name)
    ).scalars().all())
    objs = list(s.execute(
        object_query.order_by(models.BusinessObject.id)
    ).scalars().all())
    rule_states = [models.RuleState.CONFIRMED, models.RuleState.MODIFIED]
    if include_unconfirmed:
        rule_states.append(models.RuleState.AI_LABELED)
        rule_states.append(models.RuleState.CONFLICT)
    rules = list(s.execute(
        select(models.BusinessRule).where(
            models.BusinessRule.scan_id == scan_id,
            models.BusinessRule.state.in_(rule_states),
        ).order_by(models.BusinessRule.confidence.desc())).scalars().all())
    tables = list(s.execute(
        select(models.DataTable).where(models.DataTable.scan_id == scan_id)
        .order_by(models.DataTable.name)).scalars().all())
    sql_ops = list(s.execute(
        select(models.SqlOperation).where(models.SqlOperation.scan_id == scan_id)
        .order_by(models.SqlOperation.table_name)).scalars().all())
    flows = list(s.execute(
        flow_query.order_by(models.BusinessFlow.id)
    ).scalars().all())
    entries = list(s.execute(
        select(models.CodeObject).where(
            models.CodeObject.scan_id == scan_id,
            models.CodeObject.is_entry.is_(True),
            models.CodeObject.obj_type == models.ObjType.METHOD)
        .order_by(models.CodeObject.entry_kind)).scalars().all())
    chain = None
    if include_unconfirmed:
        # BusinessChain 暂无人工确认状态，因此只进入明确标记的草稿。
        chain = s.execute(
            select(models.BusinessChain)
            .where(models.BusinessChain.scan_id == scan_id)
            .order_by(models.BusinessChain.id.desc()).limit(1)
        ).scalar_one_or_none()

    return {
        "project": project, "scan": scan, "meta": meta, "stats": stats,
        "modules": modules, "domains": domains, "objects": objs,
        "rules": rules, "tables": tables, "sql_ops": sql_ops,
        "flows": flows, "entries": entries, "chain": chain,
        "include_unconfirmed": include_unconfirmed,
    }


def _state_label(state) -> str:
    return {
        "ai_labeled": "AI 识别(未确认)", "confirmed": "已确认",
        "modified": "已修改", "discarded": "已废弃", "conflict": "存在冲突",
    }.get(state, str(state))


def _header(d: dict, doc_title: str) -> str:
    p = d.get("project")
    meta = d.get("meta", {})
    return (
        f"# {p.name if p else '遗留系统'} —— {doc_title}\n\n"
        f"> 生成工具：Code2Business 业务分析平台（MVP）\n"
        f"> 代码规模：{meta.get('total_lines', 0):,} 行 / {meta.get('total_files', 0):,} 文件\n"
        f"> 编码：{meta.get('encoding_samples', {})}\n"
        f"> 知识范围：{'草稿（含 AI 未确认项）' if d.get('include_unconfirmed') else '正式（仅纳入人工已确认的业务知识）'}\n"
    )


def _table(rows: list[list[str]], widths: list[int] | None = None) -> str:
    if not rows:
        return ""
    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace(
            "\n", "<br>"
        )

    cols = max(len(r) for r in rows)
    head = "| " + " | ".join(cell(c) for c in rows[0]) + " |"
    sep = "|" + "---|" * cols
    body = "\n".join(
        "| " + " | ".join(cell(c) for c in r) + " |" for r in rows[1:]
    )
    return f"{head}\n{sep}\n{body}"


def render(doc_type: str, d: dict) -> str:
    """按文档类型渲染 Markdown。"""
    fn = {
        "overview": _doc_overview, "functions": _doc_functions,
        "rules": _doc_rules, "dictionary": _doc_dictionary,
        "apis": _doc_apis, "acceptance": _doc_acceptance,
    }[doc_type]
    return fn(d)


def _doc_overview(d: dict) -> str:
    stats = d.get("stats", {})
    lines = [_header(d, "系统现状说明书")]
    lines.append("## 1. 系统规模")
    lines.append(_table([
        ["指标", "数值"], ["代码行数", f"{d['meta'].get('total_lines', 0):,}"],
        ["代码文件", d["meta"].get("total_files", 0)],
        ["模块数", stats.get("modules", 0)], ["类数", stats.get("classes", 0)],
        ["方法数", stats.get("methods", 0)], ["调用边", stats.get("call_edges", 0)],
        ["数据表", stats.get("data_tables", 0)],
        ["SQL 操作", stats.get("sql_operations", 0)],
        ["业务域", len(d.get("domains", []))],
        ["业务对象", len(d.get("objects", []))],
        ["规则卡片", len(d.get("rules", []))],
        ["业务推演", "已生成" if d.get("chain") else "未生成"],
    ]))
    lines.append("## 2. 模块地图")
    lines.append(_table([["模块", "行数", "文件数"]] + [
        [m.name, f"{m.lines:,}", str(m.file_count)] for m in d["modules"][:30]]))
    lines.append("## 3. 业务域")
    lines.append(_table([["业务域", "说明", "核实状态"]] + [
        [x.name, x.description or "-", "已确认" if x.confirmed else "AI 推断（未确认）"]
        for x in d["domains"][:30]]))
    lines.append("## 4. 程序入口")
    lines.append(_table([["类型", "名称", "位置"]] + [
        [e.entry_kind or "-", e.name, f"{e.file_path}:{e.start_line}"]
        for e in d["entries"][:40]]))
    return "\n\n".join(lines)


def _doc_functions(d: dict) -> str:
    lines = [_header(d, "功能清单")]
    objects_by_domain: dict[int, list] = {}
    unassigned = []
    known_domain_ids = {domain.id for domain in d["domains"]}
    for obj in d["objects"]:
        if obj.domain_id in known_domain_ids:
            objects_by_domain.setdefault(obj.domain_id, []).append(obj)
        else:
            unassigned.append(obj)

    def append_object(obj) -> None:
        lines.append(f"### {obj.name}")
        lines.append(
            f"- 核实状态：{'已确认' if obj.confirmed else 'AI 推断（未确认）'}"
        )
        lines.append(f"- 说明：{obj.description or '-'}")
        lines.append(f"- 业务动作：{', '.join(obj.actions or []) or '-'}")
        lines.append(f"- 来源表：{', '.join((obj.source_tables or [])[:10]) or '-'}（未确认）")

    for dom in d["domains"][:30]:
        lines.append(f"## 业务域：{dom.name}")
        lines.append(dom.description or "-")
        domain_objects = objects_by_domain.get(dom.id, [])
        if not domain_objects:
            lines.append("（暂无归属于该业务域的业务对象）")
        for obj in domain_objects:
            append_object(obj)
    if unassigned:
        lines.append("## 未归类业务对象")
        for obj in unassigned:
            append_object(obj)
    return "\n\n".join(lines)


def _doc_rules(d: dict) -> str:
    lines = [_header(d, "业务规则清单")]
    lines.append(f"共 {len(d['rules'])} 条规则卡片，按可信度降序：")
    for r in d["rules"]:
        lines.append(f"### {r.name or '(未命名)'}")
        lines.append(_table([
            ["字段", "内容"],
            ["类型", r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type)],
            ["模块", r.module or "-"],
            ["触发方式", r.trigger or "-"],
            ["前置条件", r.pre_condition or "-"],
            ["处理逻辑", r.logic or "-"],
            ["状态变化", r.status_change or "-"],
            ["数据变化", r.data_change or "-"],
            ["后续动作", r.follow_up or "-"],
            ["异常处理", r.exception or "-"],
            ["影响范围", r.impact or "-"],
            ["代码证据", f"{r.evidence_file}:{r.evidence_lines}"],
            ["可信度", f"{r.confidence}%"],
            ["确认状态", _state_label(r.state)],
        ]))
    return "\n\n".join(lines)


def _doc_dictionary(d: dict) -> str:
    lines = [_header(d, "数据字典")]
    lines.append(f"共 {len(d['tables'])} 张表（来源：字符串 SQL 提取，未确认）：")
    lines.append(_table([["表名", "SQL 操作数", "操作类型"]] + [
        [t.name, str(sum(1 for o in d["sql_ops"] if o.table_name == t.name)),
         ",".join(sorted({o.op.value for o in d["sql_ops"]
                          if o.table_name == t.name})) or "-"]
        for t in d["tables"][:80]]))
    lines.append("## SQL 读写场景（节选）")
    lines.append(_table([["表", "操作", "字段", "位置"]] + [
        [o.table_name or "-", o.op.value, ",".join((o.fields or [])[:8]),
         f"{o.file_path}:{o.line}"] for o in d["sql_ops"][:60]]))
    return "\n\n".join(lines)


def _doc_apis(d: dict) -> str:
    lines = [_header(d, "接口清单")]
    lines.append(f"共 {len(d['entries'])} 个程序入口：")
    lines.append(_table([["类型", "名称", "文件:行", "注释"]] + [
        [e.entry_kind or "-", e.name, f"{e.file_path}:{e.start_line}",
         (e.comment or "-")[:80]] for e in d["entries"][:60]]))
    return "\n\n".join(lines)


def _doc_acceptance(d: dict) -> str:
    lines = [_header(d, "验收清单")]
    if d.get("include_unconfirmed"):
        lines.append("## 功能验收项（基于业务对象与动作生成，需确认）")
    else:
        lines.append("## 功能验收项（基于已确认业务对象与动作生成）")
    n = 0
    for o in d["objects"]:
        for act in (o.actions or [])[:10]:
            n += 1
            lines.append(f"- [ ] 验收项{n}：{o.name}——{act}")
    lines.append("## 业务主流程")
    chain = d.get("chain")
    if chain and chain.mainline:
        lines.append(" -> ".join(chain.mainline))
        lines.append(f"设计逻辑：{chain.design_narrative or '-'}")
        for a in (chain.assumptions or [])[:10]:
            lines.append(f"- [ ] 待确认：{a}")
    elif not d.get("include_unconfirmed"):
        lines.append("正式模式不纳入尚无人工确认状态的 AI 业务主线。")
    return "\n\n".join(lines)


def to_word(md: str, title: str) -> BytesIO:
    """Markdown → Word（python-docx 段落化，MVP 简化版）。"""
    doc = DocxDocument()
    doc.add_heading(title, 0)
    for line in md.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], 3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], 2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], 1)
        elif line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not any("---" in c for c in cells):
                p = doc.add_paragraph("  |  ".join(cells))
                for run in p.runs:
                    run.font.size = Pt(9)
        elif line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line.startswith("> "):
            p = doc.add_paragraph(line[2:])
            for run in p.runs:
                run.font.size = Pt(9)
                run.font.color.rgb = __import__(
                    "docx.shared", fromlist=["RGBColor"]).RGBColor(0x88, 0x88, 0x88)
        else:
            doc.add_paragraph(line)
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio
