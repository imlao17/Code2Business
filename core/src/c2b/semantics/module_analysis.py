"""模块深度分析：从模块的代码图谱知识还原「模块需求规格」。

目标：验证是否提取出足够信息来还原一套新系统。
产出：功能点、业务流程、数据模型、业务规则、外部依赖、信息缺口。
信息完备性检查：LLM 必须标注每个结论的证据来源与缺口清单。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "module_name": {"type": "string"},
        "purpose": {"type": "string"},
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "key_rules": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "description", "key_rules", "evidence"],
                "additionalProperties": False,
            },
        },
        "processes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "steps"],
                "additionalProperties": False,
            },
        },
        "data_model": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                    "tables": {"type": "array", "items": {"type": "string"}},
                    "key_fields": {"type": "array", "items": {"type": "string"}},
                    "relations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["object", "tables", "key_fields", "relations"],
                "additionalProperties": False,
            },
        },
        "business_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "condition": {"type": "string"},
                    "action": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["name", "condition", "action", "source"],
                "additionalProperties": False,
            },
        },
        "external_dependencies": {"type": "array", "items": {"type": "string"}},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["item", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["module_name", "purpose", "features", "processes",
                 "data_model", "business_rules", "external_dependencies",
                 "gaps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是资深企业系统需求分析师。给定一个代码模块的分析数据（规则卡片、流程、"
    "SQL 操作、入口、关键类），还原该模块的完整需求规格，用于新系统重建。"
    "约束：1) 只依据给定数据，每条结论尽量标注证据（文件/表/规则名）；"
    "2) gaps 必须列出所有无法从数据确认的信息（如：外部系统交互细节、"
    "批次任务的调度方式、界面交互、异常处理策略等），并给出补全建议；"
    "3) business_rules 的 condition/action 要具体到可编码的程度；"
    "4) 全部中文。"
)


def _collect(s: Session, scan_id: int, module_id: int) -> dict:
    """确定性数据收集：该模块的规则/流程/SQL/入口/关键类。"""
    mod = s.get(models.Module, module_id)
    rules = list(s.execute(
        select(models.BusinessRule).where(
            models.BusinessRule.scan_id == scan_id,
            models.BusinessRule.module == mod.name,
            models.BusinessRule.state != models.RuleState.DISCARDED)
        .order_by(models.BusinessRule.confidence.desc())).scalars().all())
    flows = []
    for f in s.execute(
            select(models.BusinessFlow)
            .where(models.BusinessFlow.scan_id == scan_id)).scalars().all():
        if mod.name in (f.entry_name or "") or any(
                mod.name in (st or "") for st in (f.business_steps or [])):
            flows.append(f)
    obj_ids = set(s.execute(
        select(models.CodeObject.id)
        .where(models.CodeObject.scan_id == scan_id,
               models.CodeObject.module_id == module_id)).scalars().all())
    sql_ops = list(s.execute(
        select(models.SqlOperation)
        .where(models.SqlOperation.scan_id == scan_id,
               models.SqlOperation.host_obj_id.in_(obj_ids))
        .order_by(models.SqlOperation.table_name)).scalars().all()) if obj_ids else []
    entries = list(s.execute(
        select(models.CodeObject)
        .where(models.CodeObject.scan_id == scan_id,
               models.CodeObject.module_id == module_id,
               models.CodeObject.is_entry.is_(True))
        .order_by(models.CodeObject.entry_kind)).scalars().all())
    hot_classes = list(s.execute(
        select(models.CodeObject.name, models.CodeObject.comment)
        .where(models.CodeObject.scan_id == scan_id,
               models.CodeObject.module_id == module_id,
               models.CodeObject.obj_type == models.ObjType.CLASS)
        .limit(25)).all())
    return {"module": mod, "rules": rules, "flows": flows,
            "sql_ops": sql_ops, "entries": entries,
            "hot_classes": hot_classes}


def _build_prompt(c: dict) -> str:
    mod = c["module"]
    lines = [f"## 模块\n{mod.name}（{mod.lines:,} 行 / {mod.file_count} 文件）"]
    lines.append("\n## 关键类（名/注释）")
    for name, comment in c["hot_classes"][:20]:
        lines.append(f"- {name}  {((comment or '')[:60]) if comment else ''}")
    if c["entries"]:
        lines.append("\n## 程序入口")
        for e in c["entries"][:10]:
            lines.append(f"- [{e.entry_kind}] {e.name} {e.file_path}:{e.start_line}")
    if c["rules"]:
        lines.append("\n## 规则卡片（含证据）")
        for r in c["rules"][:30]:
            lines.append(
                f"- {r.name}（{r.confidence}%）：{(r.logic or '-')[:80]}｜"
                f"状态 {r.status_change or '-'}｜数据 {(r.data_change or '-')[:60]}｜"
                f"证据 {r.evidence_file}:{r.evidence_lines}")
    if c["flows"]:
        lines.append("\n## 相关流程")
        for f in c["flows"][:8]:
            lines.append(f"- {f.name or f.entry_name}："
                         f"{' -> '.join((f.business_steps or [])[:8])}")
    if c["sql_ops"]:
        lines.append("\n## SQL 操作（表/字段/读写）")
        seen = set()
        for o in c["sql_ops"][:60]:
            key = (o.table_name, o.op.value)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {o.table_name} [{o.op.value}] "
                         f"字段 {','.join((o.fields or [])[:8])}")
    lines.append("\n请输出该模块的需求规格 JSON。")
    return "\n".join(lines)


def render_markdown(content: dict) -> str:
    """需求规格 → Markdown（可下载的交付文档）。"""
    out = [f"# 模块需求规格：{content.get('module_name', '')}",
           "",
           f"## 模块职责\n{content.get('purpose', '-')}",
           "",
           "## 功能点"]
    for i, f in enumerate(content.get("features", []), 1):
        out.append(f"### {i}. {f.get('name')}")
        out.append(f"{f.get('description', '-')}")
        if f.get("key_rules"):
            out.append("关键规则：" + "；".join(f["key_rules"][:5]))
        if f.get("evidence"):
            out.append(f"证据：{f['evidence']}")
    out += ["", "## 业务流程"]
    for p in content.get("processes", []):
        out.append(f"### {p.get('name', '')}")
        for st in p.get("steps", []):
            out.append(f"- {st}")
    out += ["", "## 数据模型"]
    for d in content.get("data_model", []):
        out.append(f"### {d.get('object', '')}")
        out.append(f"- 表：{', '.join(d.get('tables', []))}")
        if d.get("key_fields"):
            out.append(f"- 关键字段：{', '.join(d['key_fields'][:12])}")
        for r in d.get("relations", []):
            out.append(f"- 关系：{r}")
    out += ["", "## 业务规则"]
    for r in content.get("business_rules", []):
        out.append(f"- **{r.get('name', '')}**：当{r.get('condition', '-')}，则{r.get('action', '-')}（来源：{r.get('source', '-')}）")
    out += ["", "## 外部依赖"]
    for d in content.get("external_dependencies", []):
        out.append(f"- {d}")
    out += ["", "## 信息缺口（新系统重建需补全）"]
    for g in content.get("gaps", []):
        out.append(f"- {g.get('item', '')}——建议：{g.get('suggestion', '')}")
    return "\n".join(out)


def materialize_analysis(analysis: models.ModuleAnalysis,
                         canonical_module_name: str) -> tuple[dict, str]:
    """Render an analysis with module identity from the deterministic graph.

    Older rows may contain an LLM-generated label in both JSON and Markdown.
    Re-rendering at the delivery boundary repairs those rows without relying
    on database-specific JSON update syntax.
    """
    content = dict(analysis.content) if isinstance(analysis.content, dict) else {}
    content["module_name"] = canonical_module_name
    if not isinstance(content.get("purpose"), str):
        content["purpose"] = ""
    nested_lists = {
        "features": ("key_rules",),
        "processes": ("steps",),
        "data_model": ("tables", "key_fields", "relations"),
        "business_rules": (),
        "gaps": (),
    }
    for field, list_fields in nested_lists.items():
        items = content.get(field)
        normalized_items = [dict(item) for item in items
                            if isinstance(item, dict)] if isinstance(items, list) else []
        for item in normalized_items:
            for list_field in list_fields:
                values = item.get(list_field)
                item[list_field] = list(values) if isinstance(values, list) else []
        content[field] = normalized_items
    dependencies = content.get("external_dependencies")
    content["external_dependencies"] = (
        [str(item) for item in dependencies]
        if isinstance(dependencies, list) else []
    )
    return content, render_markdown(content)


def analyze_module(s: Session, scan_id: int, module_id: int,
                   client: LLMClient, force: bool = False) -> dict:
    """模块深度分析：收集 + LLM 生成 + 落库。返回需求规格 dict。
    force=True 时绕过缓存（既有 LLM 缓存与已存分析均重新生成）。"""
    module = s.get(models.Module, module_id)
    if module is None or module.scan_id != scan_id:
        raise ValueError("模块不存在或不属于当前扫描")
    existing = s.execute(
        select(models.ModuleAnalysis).where(
            models.ModuleAnalysis.scan_id == scan_id,
            models.ModuleAnalysis.module_id == module_id)
    ).scalar_one_or_none()
    if existing and not force:
        content, markdown = materialize_analysis(existing, module.name)
        return {"cached": True, "content": content,
                "markdown": markdown}

    c = _collect(s, scan_id, module_id)
    result = client.chat_json(SYSTEM_PROMPT, _build_prompt(c), schema=SPEC_SCHEMA)
    content = {k: result.get(k, [] if isinstance(result.get(k), list) else "")
               for k in ("module_name", "purpose", "features", "processes",
                         "data_model", "business_rules",
                         "external_dependencies", "gaps")}
    # Module identity comes from the deterministic graph, never from an LLM
    # display label.  This also prevents duplicate/colliding export filenames.
    content["module_name"] = module.name
    md = render_markdown(content)
    if existing:
        # 覆盖既有记录（重新分析）
        existing.content = content
        existing.markdown = md
        existing.model = client.model
        existing.module_name = module.name
        analysis = existing
    else:
        analysis = models.ModuleAnalysis(
            scan_id=scan_id, module_id=module_id,
            module_name=module.name, content=content,
            markdown=md, model=client.model)
        s.add(analysis)
    s.commit()
    return {"cached": False, "force": force, "content": content,
            "markdown": md}
