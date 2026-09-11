"""文档生成（M5a）：从已确认业务知识渲染正式交付文档。

模板：Jinja2 内嵌定义（MVP 无外部模板文件，保持自包含）。
导出：Markdown（默认）+ Word（python-docx 段落化）。
原则：正式文档只纳入人工确认的业务域、对象、流程及已确认/人工修改
的规则；尚无确认状态的 AI 主线也只进入草稿。已废弃规则始终排除。
"""
import re
from datetime import date
from io import BytesIO

from docx import Document as DocxDocument
from docx.shared import Pt, RGBColor
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .runtime_evidence import runtime_level

DOC_TYPES = {
    "combined": ("客户交付总报告",
                 "面向客户与业务人员的单一合并报告：概览、功能、流程、核心规则、验收与待确认"),
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
    allowed_runtime_targets = {("rule", rule.id) for rule in rules}
    allowed_runtime_targets.update(("flow", flow.id) for flow in flows)
    if chain is not None:
        allowed_runtime_targets.add(("chain", chain.id))
    runtime_evidence = [item for item in s.execute(
        select(models.RuntimeEvidence).where(
            models.RuntimeEvidence.scan_id == scan_id
        ).order_by(models.RuntimeEvidence.id.desc())
    ).scalars().all() if (item.target_type, item.target_id) in allowed_runtime_targets]
    questions = list(s.execute(
        select(models.Question).where(
            models.Question.scan_id == scan_id,
            models.Question.status == "open",
        ).order_by(models.Question.id)
    ).scalars().all())

    return {
        "project": project, "scan": scan, "meta": meta, "stats": stats,
        "modules": modules, "domains": domains, "objects": objs,
        "rules": rules, "tables": tables, "sql_ops": sql_ops,
        "flows": flows, "entries": entries, "chain": chain,
        "runtime_evidence": runtime_evidence, "questions": questions,
        "include_unconfirmed": include_unconfirmed,
    }


def _runtime_for(d: dict, target_type: str, target_id: int) -> list:
    return [
        item for item in d.get("runtime_evidence", [])
        if item.target_type == target_type and item.target_id == target_id
    ]


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
        f"> 生成工具：Code2Business 业务分析平台\n"
        f"> 代码规模：{meta.get('total_lines', 0):,} 行 / {meta.get('total_files', 0):,} 文件\n"
        f"> 编码：{meta.get('encoding_samples', {})}\n"
        f"> 知识范围：{'草稿（含 AI 未确认项）' if d.get('include_unconfirmed') else '正式（仅纳入人工已确认的业务知识）'}\n"
        "> 证据口径：V/代码定位表示静态证据；R0-R3 表示独立运行验证；人工确认状态单独展示\n"
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
        "combined": _doc_combined,
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
        ["运行证据记录", len(d.get("runtime_evidence", []))],
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
    if d.get("flows"):
        lines.append("## 5. 业务流程与运行验证")
        lines.append(_table([["业务流程", "AI可信度", "运行验证", "人工确认"]] + [
            [flow.name or "-", f"{flow.confidence}%",
             runtime_level(_runtime_for(d, "flow", flow.id)),
             "已确认" if flow.confirmed else "未确认"]
            for flow in d["flows"][:40]
        ]))
    return "\n\n".join(lines)


def _functions_body(d: dict, level: int = 2) -> list[str]:
    """业务域 → 业务对象 功能层级正文（供功能清单与总报告复用）。"""
    lines: list[str] = []
    dom_heading = f"{'#' * level} 业务域：{{}}"
    obj_heading = f"{'#' * (level + 1)} {{}}"
    objects_by_domain: dict[int, list] = {}
    unassigned = []
    known_domain_ids = {domain.id for domain in d["domains"]}
    for obj in d["objects"]:
        if obj.domain_id in known_domain_ids:
            objects_by_domain.setdefault(obj.domain_id, []).append(obj)
        else:
            unassigned.append(obj)

    def append_object(obj) -> None:
        lines.append(obj_heading.format(obj.name))
        lines.append(
            f"- 核实状态：{'已确认' if obj.confirmed else 'AI 推断（未确认）'}"
        )
        lines.append(f"- 说明：{obj.description or '-'}")
        lines.append(f"- 业务动作：{', '.join(obj.actions or []) or '-'}")
        lines.append(f"- 来源表：{', '.join((obj.source_tables or [])[:10]) or '-'}（未确认）")

    for dom in d["domains"][:30]:
        lines.append(dom_heading.format(dom.name))
        lines.append(dom.description or "-")
        domain_objects = objects_by_domain.get(dom.id, [])
        if not domain_objects:
            lines.append("（暂无归属于该业务域的业务对象）")
        for obj in domain_objects:
            append_object(obj)
    if unassigned:
        lines.append(f"{'#' * level} 未归类业务对象")
        for obj in unassigned:
            append_object(obj)
    return lines


def _doc_functions(d: dict) -> str:
    lines = [_header(d, "功能清单")]
    lines.extend(_functions_body(d, level=2))
    return "\n\n".join(lines)


def _rules_body(d: dict) -> list[str]:
    """规则卡片全文正文（供业务规则清单与总报告附录复用）。"""
    lines = [f"共 {len(d['rules'])} 条规则卡片，按可信度降序："]
    for r in d["rules"]:
        runtime_items = _runtime_for(d, "rule", r.id)
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
            ["AI可信度", f"{r.confidence}%"],
            ["运行验证", runtime_level(runtime_items)],
            ["人工确认状态", _state_label(r.state)],
        ]))
        if runtime_items:
            lines.append(_table([["运行等级", "环境", "类型", "摘要", "引用"]] + [
                [item.level, item.environment, item.evidence_type,
                 item.summary, item.reference or "-"] for item in runtime_items
            ]))
    return lines


def _doc_rules(d: dict) -> str:
    lines = [_header(d, "业务规则清单")]
    lines.extend(_rules_body(d))
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


def _acceptance_items(d: dict) -> list[str]:
    """功能验收项 checkbox（供验收清单与总报告复用）。"""
    items = []
    n = 0
    for o in d["objects"]:
        for act in (o.actions or [])[:10]:
            n += 1
            items.append(f"- [ ] 验收项{n}：{o.name}——{act}")
    return items


def _doc_acceptance(d: dict) -> str:
    lines = [_header(d, "验收清单")]
    if d.get("include_unconfirmed"):
        lines.append("## 功能验收项（基于业务对象与动作生成，需确认）")
    else:
        lines.append("## 功能验收项（基于已确认业务对象与动作生成）")
    lines.extend(_acceptance_items(d))
    lines.append("## 业务主流程")
    chain = d.get("chain")
    if chain and chain.mainline:
        lines.append(" -> ".join(chain.mainline))
        lines.append(f"设计逻辑：{chain.design_narrative or '-'}")
        lines.append(
            f"运行验证：{runtime_level(_runtime_for(d, 'chain', chain.id))}"
        )
        for a in (chain.assumptions or [])[:10]:
            lines.append(f"- [ ] 待确认：{a}")
    elif not d.get("include_unconfirmed"):
        lines.append("正式模式不纳入尚无人工确认状态的 AI 业务主线。")
    return "\n\n".join(lines)


_ENTRY_KIND_LABEL = {
    "batch": "批处理任务", "api": "接口/在线服务", "schedule": "定时任务",
    "mq": "消息处理", "gui": "界面操作",
}


def _truncate(text: str | None, width: int = 60) -> str:
    text = text or "-"
    return text if len(text) <= width else text[:width] + "…"


def _doc_combined(d: dict) -> str:
    """客户交付总报告：把分散的交付文档合成一份面向非技术读者的单一报告。

    章节口径与六份专项文档一致，但做了三件事：去重（业务域/入口只出现一次）、
    白话化（执行摘要与入口概览不暴露代码术语）、划清边界（AI 未确认内容只在
    草稿版出现，并集中到"待确认事项"）。
    """
    from . import __version__
    stats = d.get("stats", {})
    meta = d.get("meta", {})
    rules = d.get("rules", [])
    chain = d.get("chain")
    draft = bool(d.get("include_unconfirmed"))
    project = d.get("project")

    lines = [_header(d, "客户交付总报告")]
    lines.append("## 阅读说明")
    lines.append(
        "本报告由 Code2Business 对系统源代码做确定性扫描、再经 AI 归纳业务含义后自动生成，"
        "面向业务人员、项目经理与验收方，不需要会读代码也能使用。"
        "每个业务结论都可追溯到代码位置；AI 归纳内容均标注可信度，"
        "并区分「AI 推断（未确认）」与「已人工确认」。"
    )

    # ---------- 执行摘要 ----------
    confirmed_n = sum(1 for r in rules if r.state in (
        models.RuleState.CONFIRMED, models.RuleState.MODIFIED))
    entry_names: dict[str, list[str]] = {}
    for e in d.get("entries", []):
        kind = _ENTRY_KIND_LABEL.get(e.entry_kind, e.entry_kind or "其他")
        entry_names.setdefault(kind, []).append(e.name or "(未命名)")
    entry_counts = {kind: len(names) for kind, names in entry_names.items()}
    entry_summary = "、".join(f"{kind} {n} 个" for kind, n in entry_counts.items()) or "未识别到程序入口"
    lines.append("## 执行摘要")
    summary = [
        f"本次分析的对象是《{project.name if project else '遗留系统'}》，"
        f"共 {meta.get('total_lines', 0):,} 行代码、{meta.get('total_files', 0):,} 个文件，"
        f"划分为 {stats.get('modules', 0)} 个功能模块，对外提供{entry_summary}。",
        f"从代码中归纳出 {len(d.get('domains', []))} 个业务域、"
        f"{len(d.get('objects', []))} 个业务对象、{len(rules)} 条业务规则、"
        f"{len(d.get('flows', []))} 条业务流程。",
    ]
    if draft:
        summary.append(
            f"其中人工已确认规则 {confirmed_n} 条。本版本为草稿，包含 AI 未确认内容，"
            "请在「待确认事项」与规则核实后出具正式版。"
        )
    else:
        summary.append("本报告为正式版，仅包含已人工确认的业务知识。")
    if chain and chain.mainline:
        summary.append("系统业务主线：" + " → ".join(chain.mainline) + "。")
    lines.extend(summary)

    # ---------- 1. 系统概览 ----------
    lines.append("## 1. 系统概览")
    lines.append(_table([
        ["指标", "数值"], ["代码行数", f"{meta.get('total_lines', 0):,}"],
        ["代码文件", meta.get("total_files", 0)],
        ["功能模块", stats.get("modules", 0)], ["类", stats.get("classes", 0)],
        ["方法", stats.get("methods", 0)], ["数据表", stats.get("data_tables", 0)],
        ["业务域", len(d.get("domains", []))],
        ["业务对象", len(d.get("objects", []))],
        ["业务规则", len(rules)],
        ["业务流程", len(d.get("flows", []))],
        ["程序入口", len(d.get("entries", []))],
    ]))

    # ---------- 2. 业务功能 ----------
    lines.append("## 2. 业务功能")
    lines.append("按业务域归组展示系统承担的业务功能；每个对象列出它可以做的业务动作。")
    lines.extend(_functions_body(d, level=3))

    # ---------- 3. 业务流程与业务主线 ----------
    lines.append("## 3. 业务流程与业务主线")
    if d.get("flows"):
        lines.append(_table([["业务流程", "AI可信度", "运行验证", "人工确认"]] + [
            [flow.name or "-", f"{flow.confidence}%",
             runtime_level(_runtime_for(d, "flow", flow.id)),
             "已确认" if flow.confirmed else "未确认"]
            for flow in d["flows"][:40]
        ]))
    else:
        lines.append("暂无业务流程记录。")
    if chain and chain.mainline:
        lines.append(f"### 业务主线（{'AI 推演，草稿' if draft else '已确认'}）")
        lines.append(" → ".join(chain.mainline))
        lines.append(f"设计逻辑：{chain.design_narrative or '-'}")
    elif not draft:
        lines.append("正式版不包含尚无人工确认状态的 AI 业务主线；如需查看，请生成草稿版。")

    # ---------- 4. 核心业务规则 ----------
    lines.append("## 4. 核心业务规则（按可信度取前 20 条）")
    if rules:
        lines.append(_table([
            ["规则", "类型", "处理逻辑（摘要）", "AI可信度", "人工确认状态"]] + [
            [r.name or "(未命名)",
             r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type),
             _truncate(r.logic), f"{r.confidence}%", _state_label(r.state)]
            for r in rules[:20]
        ]))
        lines.append(f"系统共归纳 {len(rules)} 条业务规则；"
                     "完整字段与代码证据见配套的《业务规则清单》。")
    else:
        lines.append("暂无业务规则（正式版需先在规则页人工确认；草稿版需先完成 AI 标注）。")

    # ---------- 5. 数据概览 ----------
    lines.append("## 5. 数据概览")
    if d.get("tables"):
        lines.append(_table([["数据表", "SQL 操作数", "操作类型"]] + [
            [t.name, str(sum(1 for o in d["sql_ops"] if o.table_name == t.name)),
             ",".join(sorted({o.op.value for o in d["sql_ops"]
                              if o.table_name == t.name})) or "-"]
            for t in d["tables"][:40]
        ]))
        lines.append("完整表清单与 SQL 读写场景见配套的《数据字典》。")
    else:
        lines.append("未从代码中提取到数据表。")

    # ---------- 6. 程序入口概览 ----------
    lines.append("## 6. 程序入口概览")
    if entry_names:
        lines.append(_table([["入口类型", "数量", "示例（名称）"]] + [
            [kind, str(entry_counts[kind]),
             "、".join(entry_names[kind][:6]) + ("等" if entry_counts[kind] > 6 else "")]
            for kind in entry_names
        ]))
        lines.append("完整入口清单（含代码位置）见配套的《接口清单》与《系统现状说明书》。")
    else:
        lines.append("未识别到程序入口。")

    # ---------- 7. 验收清单 ----------
    lines.append("## 7. 验收清单")
    lines.append("以下验收项由业务对象与业务动作自动生成，可作为用户验收的检查单：")
    acceptance_items = _acceptance_items(d)
    if acceptance_items:
        lines.extend(acceptance_items)
    else:
        lines.append("（暂无可生成的验收项）")

    # ---------- 8. 待确认事项 ----------
    lines.append("## 8. 待确认事项")
    questions = d.get("questions", [])
    assumptions = list((chain.assumptions or [])[:10]) if (draft and chain) else []
    if questions or assumptions:
        if questions:
            lines.append(_table([["序号", "类型", "问题描述", "建议"]] + [
                [str(i), q.qtype or "-", _truncate(q.desc, 80),
                 _truncate(q.suggestion, 80)]
                for i, q in enumerate(questions[:30], 1)
            ]))
        for a in assumptions:
            lines.append(f"- [ ] 待确认：{a}")
        if draft:
            lines.append("以上事项确认后，重新生成即可得到不含未确认内容的正式版报告。")
    else:
        lines.append("当前没有待确认事项。")

    # ---------- 9. 生成说明与可信度声明 ----------
    lines.append("## 9. 生成说明与可信度声明")
    lines.append(f"- 生成工具：Code2Business v{__version__}（生成日期 {date.today().isoformat()}）")
    lines.append("- 生成方式：先对源代码做确定性扫描得到调用图谱与数据访问证据，再由 AI 归纳业务含义；"
                 "所有结论保留代码证据位置。")
    lines.append("- 可信度口径：AI 可信度、运行验证（R0-R3）与人工确认状态是三个独立维度，互不替代；"
                 "本报告不代表系统已经过实际运行验证。")
    lines.append("- 使用边界：AI 归纳内容需经业务/技术人员确认后方可作为重构、验收或对外承诺的正式依据。")

    # ---------- 附录 ----------
    if draft and rules:
        lines.append("## 附录：完整业务规则清单（草稿）")
        lines.extend(_rules_body(d))
    return "\n\n".join(lines)


def _strip_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _word_paragraph(doc, text: str, *, style: str | None = None,
                    size: Pt | None = None, color: RGBColor | None = None) -> None:
    """写一个段落，支持 **加粗** 行内标记。"""
    p = doc.add_paragraph(style=style)
    for part in re.split(r"(\*\*.+?\*\*)", text):
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**") and len(part) >= 5
        run = p.add_run(part[2:-2] if bold else part)
        if bold:
            run.bold = True
        if size is not None:
            run.font.size = size
        if color is not None:
            run.font.color.rgb = color


def _word_table(doc, block: list[str]) -> None:
    """把连续的 Markdown 表格行写成真 Word 表格（表头加粗、细边框）。"""
    rows: list[list[str]] = []
    for line in block:
        # 先按未转义的竖线切列，再还原单元格内的 \| 转义
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue  # 分隔行
        rows.append([c.replace("\\|", "|") for c in cells])
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass  # 非默认模板可能没有该样式，退化为无边框表格
    for ri, row in enumerate(rows):
        for ci in range(cols):
            para = table.cell(ri, ci).paragraphs[0]
            parts = (row[ci] if ci < len(row) else "").split("<br>")
            run = para.add_run(parts[0])
            run.font.size = Pt(9)
            run.bold = ri == 0
            for extra in parts[1:]:
                run.add_break()
                follow = para.add_run(extra)
                follow.font.size = Pt(9)
                follow.bold = ri == 0


def to_word(md: str, title: str) -> BytesIO:
    """Markdown → Word（python-docx）。

    v0.4 起表格渲染为真 Word 表格（此前被拍平成文本行），并支持多级
    标题、项目符号、checkbox 与 **加粗** 行内标记。
    """
    gray = RGBColor(0x88, 0x88, 0x88)
    doc = DocxDocument()
    doc.add_heading(title, 0)
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            _word_table(doc, block)
            continue
        if line.startswith("### "):
            doc.add_heading(_strip_bold(line[4:]), 3)
        elif line.startswith("## "):
            doc.add_heading(_strip_bold(line[3:]), 2)
        elif line.startswith("# "):
            doc.add_heading(_strip_bold(line[2:]), 1)
        elif line.startswith("- [ ] "):
            _word_paragraph(doc, "□ " + line[6:], style="List Bullet")
        elif line.startswith("- "):
            _word_paragraph(doc, line[2:], style="List Bullet")
        elif line.startswith("> "):
            _word_paragraph(doc, line[2:], size=Pt(9), color=gray)
        elif line:
            _word_paragraph(doc, line)
        i += 1
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio
