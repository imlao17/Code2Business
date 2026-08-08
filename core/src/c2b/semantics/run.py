"""语义标注管线编排（M3）：业务域 → 候选方法排序 → 规则卡片 → 落库。"""
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient, usage_summary
from . import domains as dom_mod
from . import rules as rules_mod


def _map_rule_type(t: str | None) -> models.RuleType:
    """LLM 输出的规则类型宽容映射（中英文变体 → 枚举）。"""
    s = (t or "").strip().lower()
    table = {
        "validation": "validation", "校验": "validation", "校验规则": "validation",
        "校验类": "validation", "检查": "validation", "判断": "validation",
        "calculation": "calculation", "计算": "calculation", "计算规则": "calculation",
        "计算类": "calculation", "运算": "calculation",
        "status": "status", "状态": "status", "状态规则": "status", "状态流转": "status",
        "permission": "permission", "权限": "permission", "权限规则": "permission",
        "trigger": "trigger", "自动触发": "trigger", "触发": "trigger",
        "自动": "trigger", "联动": "trigger",
        "exception": "exception", "异常": "exception", "异常处理": "exception",
        "异常规则": "exception",
    }
    return models.RuleType(table.get(s, "other"))


# 工具方法黑名单：高热度但无业务语义（实测 Top 方法全是这些）
UTIL_METHODS = {
    "equals", "hashcode", "tostring", "compareto", "printstacktrace",
    "trim", "intvalue", "longvalue", "doublevalue", "floatvalue", "nexttoken",
    "hasmoretokens", "clone", "finalize", "wait", "notify", "notifyall",
    "getclass", "main", "readline", "readalllines", "close", "flush",
    "iterator", "next", "hasnext", "size", "clear", "contains", "isempty",
    "isblank", "isnull", "indexof", "substring", "replace", "split", "join",
    "format", "parse", "valueof", "concat", "touppercase", "tolowercase",
    "append", "insertchar", "setlength", "getlength",
}
UTIL_CLASS_HINTS = ("util", "helper", "constant", "base", "exception",
                    "test", "model", "dto", "vo", "bean", "common",
                    "listener", "adapter", "handler", "pool", "cache")
BIZ_VERBS = (
    "approve", "submit", "calc", "compute", "update", "insert", "delete",
    "create", "regist", "cancel", "confirm", "finish", "check", "validate",
    "import", "export", "send", "receive", "settle", "pay", "receipt",
    "issue", "plan", "order", "match", "alloc", "adjust", "judge", "decide",
    "release", "lock", "unlock", "close", "open", "start", "stop", "run",
    "proc", "execute", "sync", "rollback", "commit", "count", "sum",
    "total", "balance", "stock", "inventory", "ship", "deliver", "claim",
)


def _method_score(m: models.CodeObject, sql_host_ids: set[int]) -> int:
    """业务评分：选业务方法，过滤工具方法（实测热度≈工具方法）。"""
    name = (m.name or "").lower()
    if name in UTIL_METHODS:
        return -100
    if name.startswith(("get", "set", "is")) and len(name) <= 8:
        return -80  # getter/setter
    score = 0
    mod = m.module or ""
    if "businese" in mod:
        score += 4
    elif "common" in mod:
        score += 1
    if any(v in name for v in BIZ_VERBS):
        score += 3
    size = (m.end_line or 0) - (m.start_line or 0)
    if 15 <= size <= 250:
        score += 2
    elif size > 500:
        score -= 2
    if m.id in sql_host_ids:
        score += 2
    if m.is_entry:
        score += 1
    return score


def pick_methods(s: Session, scan_id: int, limit: int,
                 entry_only: bool = False) -> list[models.CodeObject]:
    """候选方法：业务评分优先（过滤工具方法），同分按调用热度。

    实测教训：按热度直接选会选中 get/equals/trim 等工具方法，
    LLM 正确判定为非业务规则导致产出趋近于零，因此改为业务评分制。
    """
    # 操作过业务表的方法（评分加权）
    sql_host_ids = set(s.execute(
        select(models.SqlOperation.host_obj_id)
        .where(models.SqlOperation.scan_id == scan_id,
               models.SqlOperation.host_obj_id.isnot(None))).scalars().all())

    in_degree = (
        select(models.CallEdge.to_obj_id, func.count(models.CallEdge.id).label("n"))
        .where(models.CallEdge.scan_id == scan_id,
               models.CallEdge.to_obj_id.isnot(None))
        .group_by(models.CallEdge.to_obj_id).subquery())
    rows = s.execute(
        select(models.CodeObject, models.Module.name.label("mod_name"),
               func.coalesce(in_degree.c.n, 0).label("heat"))
        .join(in_degree, in_degree.c.to_obj_id == models.CodeObject.id,
              isouter=True)
        .join(models.Module, models.Module.id == models.CodeObject.module_id,
              isouter=True)
        .where(models.CodeObject.scan_id == scan_id,
               models.CodeObject.obj_type == models.ObjType.METHOD)).all()

    scored = []
    for m, mod_name, heat in rows:
        m.module = mod_name
        if entry_only and not m.is_entry:
            continue
        sc = _method_score(m, sql_host_ids)
        if sc <= 0:
            continue
        # 最终排序：业务评分 → 入口 → 热度
        scored.append((sc, 1 if m.is_entry else 0, heat, m))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [m for _, _, _, m in scored[:limit]]


def tables_for_methods(s: Session, scan_id: int) -> dict[int, list[str]]:
    rows = s.execute(
        select(models.SqlOperation.host_obj_id, models.SqlOperation.table_name)
        .where(models.SqlOperation.scan_id == scan_id,
               models.SqlOperation.host_obj_id.isnot(None))
        .distinct()).all()
    out: dict[int, list[str]] = {}
    for obj_id, table in rows:
        out.setdefault(obj_id, []).append(table)
    return out


def run_label(s: Session, project_id: int, limit: int, concurrency: int,
              entry_only: bool = False, with_objects: bool = True,
              with_flows: bool = True, client: LLMClient | None = None,
              progress_cb=None) -> dict:
    """执行标注管线，返回统计。

    All model calls except the final chain inference are prepared before any
    destructive write.  Human decisions are immutable inputs to a re-label:
    confirmed, modified and discarded rules are preserved, and only machine
    generated rows are replaced.  Database changes commit once at the end so
    a persistence failure cannot leave half a semantic graph behind.
    """
    from . import chain as chain_mod
    from . import flows as flows_mod
    from . import objects as objects_mod
    if limit < 0:
        raise ValueError("limit 不能小于 0")
    if concurrency < 1:
        raise ValueError("concurrency 必须大于 0")
    client = client or LLMClient()
    project = s.get(models.Project, project_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在")
    scan = s.execute(
        select(models.Scan).where(
            models.Scan.project_id == project_id,
            models.Scan.status == models.ScanStatus.DONE,
        )
        .order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
    if scan is None:
        raise ValueError("项目还没有已完成的扫描，先执行 c2b scan")

    root = Path(project.repo_path)
    if not root.exists():
        raise ValueError(f"项目代码目录不存在: {root}")

    # ---------- Prepare model outputs without mutating semantic tables ----------
    modules = s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
        .order_by(models.Module.lines.desc())).scalars().all()
    module_name_by_id = {module.id: module.name for module in modules}
    class_names = {}
    for mod in modules:
        names = s.execute(
            select(models.CodeObject.name)
            .where(models.CodeObject.scan_id == scan.id,
                   models.CodeObject.module_id == mod.id,
                   models.CodeObject.obj_type == models.ObjType.CLASS)
            .limit(3)).scalars().all()
        class_names[mod.id] = list(names)

    # S1: 业务域识别。Domain failure is fatal because rules and objects use
    # this mapping; retaining the previous graph is safer than partial output.
    try:
        result = dom_mod.recognize_domains(client, modules, class_names)
        merged: dict[str, dict] = {}
        for d in result.get("domains", []):
            name = (d.get("name") or "").strip()
            if not name or name == "未归类":
                continue
            if name not in merged:
                merged[name] = {"description": d.get("description"),
                                "modules": set(d.get("modules", []))}
            else:
                merged[name]["modules"].update(d.get("modules", []))
        valid_modules = set(module_name_by_id.values())
        domain_specs = [
            {
                "name": name,
                "description": info["description"],
                "modules": sorted(set(info["modules"]) & valid_modules),
            }
            for name, info in merged.items()
        ]
    except Exception as e:
        s.rollback()
        raise RuntimeError(f"业务域识别失败，已保留原语义数据: {e}") from e

    human_states = [
        models.RuleState.CONFIRMED,
        models.RuleState.MODIFIED,
        models.RuleState.DISCARDED,
    ]
    preserved_human_rule_count = s.execute(
        select(func.count(models.BusinessRule.id)).where(
            models.BusinessRule.scan_id == scan.id,
            models.BusinessRule.state.in_(human_states),
        )
    ).scalar() or 0
    preserved_method_ids = set(s.execute(
        select(models.BusinessRule.source_method_id).where(
            models.BusinessRule.scan_id == scan.id,
            models.BusinessRule.state.in_(human_states),
            models.BusinessRule.source_method_id.isnot(None),
        )
    ).scalars().all())

    # S3: 规则卡片。Already reviewed methods are deliberately not re-billed.
    # Apply entry-only before the limit.  Filtering an already truncated list
    # can return zero entries when higher-scored service methods occupy it.
    methods = pick_methods(s, scan.id, limit, entry_only=entry_only)
    methods = [method for method in methods if method.id not in preserved_method_ids]

    tables = tables_for_methods(s, scan.id)
    cb_rules = (lambda d, t: progress_cb(d, t, "rules")) if progress_cb else None
    results = rules_mod.label_methods(client, methods, root, tables,
                                      concurrency, cb_rules)
    if methods and not any(error is None and card is not None
                           for _, card, error in results):
        s.rollback()
        raise RuntimeError("全部规则标注请求失败，已保留原语义数据")

    warnings: list[str] = []
    object_specs: list[dict] | None = None
    if with_objects:
        try:
            if progress_cb:
                progress_cb(0, 0, "objects")
            object_tables = objects_mod.collect_tables(s, scan.id)
            object_classes = objects_mod.collect_classes(s, scan.id)
            object_specs = (
                objects_mod.recognize(client, object_tables, object_classes)
                if object_tables or object_classes else []
            )
        except Exception as e:
            object_specs = None
            warnings.append(f"业务对象识别失败，保留原结果: {e}")

    flow_specs: list[dict] | None = None
    if with_flows:
        if progress_cb:
            progress_cb(0, 0, "flows")
        flow_specs = []
        attempted = failed_flows = 0
        for entry in flows_mod.pick_entries(s, scan.id, limit=5):
            call_nodes = flows_mod.call_chain(s, scan.id, entry.id)
            if not call_nodes:
                continue
            attempted += 1
            try:
                flow = flows_mod.recognize(client, entry, call_nodes)
                if flow.get("business_steps"):
                    flow_specs.append({
                        **flow,
                        "entry": entry,
                        "tech_steps": [node.name for node in call_nodes[:15]],
                    })
            except Exception:
                failed_flows += 1
        if attempted and failed_flows == attempted:
            flow_specs = None
            warnings.append("业务流程识别全部失败，保留原结果")

    if progress_cb:
        # This callback occurs before the write transaction to avoid a second
        # SQLite connection waiting on our write lock.
        progress_cb(0, 0, "persisting")

    # ---------- Replace machine-generated rows in one transaction ----------
    try:
        existing_domains = {
            domain.name: domain for domain in s.execute(
                select(models.BusinessDomain).where(
                    models.BusinessDomain.scan_id == scan.id
                )
            ).scalars().all()
        }
        for spec in domain_specs:
            domain = existing_domains.get(spec["name"])
            if domain is None:
                domain = models.BusinessDomain(
                    scan_id=scan.id, name=spec["name"], confidence=70
                )
                s.add(domain)
                existing_domains[spec["name"]] = domain
            if not domain.confirmed:
                domain.description = spec["description"]
                domain.modules = spec["modules"]
                domain.confidence = 70
        s.flush()
        domain_map: dict[str, int] = {}
        for spec in domain_specs:
            for module_name in spec["modules"]:
                domain_map.setdefault(module_name, existing_domains[spec["name"]].id)

        generated_rule_ids = list(s.execute(
            select(models.BusinessRule.id).where(
                models.BusinessRule.scan_id == scan.id,
                models.BusinessRule.state.in_([
                    models.RuleState.AI_LABELED,
                    models.RuleState.CONFLICT,
                ]),
            )
        ).scalars().all())
        if generated_rule_ids:
            s.query(models.Question).filter(
                models.Question.rule_id.in_(generated_rule_ids)
            ).delete(synchronize_session=False)
            s.query(models.BusinessRule).filter(
                models.BusinessRule.id.in_(generated_rule_ids)
            ).delete(synchronize_session="fetch")
        s.query(models.Question).filter(
            models.Question.scan_id == scan.id,
            models.Question.qtype == "llm_error",
            models.Question.status == "open",
        ).delete(synchronize_session=False)

        # Build evidence-to-domain hints for business objects.
        class_modules: dict[str, set[str]] = {}
        for class_name, module_name in s.execute(
            select(models.CodeObject.name, models.Module.name)
            .join(models.Module, models.Module.id == models.CodeObject.module_id)
            .where(
                models.CodeObject.scan_id == scan.id,
                models.CodeObject.obj_type == models.ObjType.CLASS,
            )
        ).all():
            class_modules.setdefault(class_name, set()).add(module_name)
        table_modules: dict[str, set[str]] = {}
        for table_name, module_name in s.execute(
            select(models.SqlOperation.table_name, models.Module.name)
            .join(models.CodeObject,
                  models.CodeObject.id == models.SqlOperation.host_obj_id)
            .join(models.Module, models.Module.id == models.CodeObject.module_id)
            .where(
                models.SqlOperation.scan_id == scan.id,
                models.SqlOperation.table_name.isnot(None),
            )
        ).all():
            table_modules.setdefault((table_name or "").lower(), set()).add(module_name)

        n_objects = 0
        if object_specs is not None:
            preserved_object_names = set(s.execute(
                select(models.BusinessObject.name).where(
                    models.BusinessObject.scan_id == scan.id,
                    models.BusinessObject.confirmed.is_(True),
                )
            ).scalars().all())
            s.query(models.BusinessObject).filter(
                models.BusinessObject.scan_id == scan.id,
                models.BusinessObject.confirmed.is_not(True),
            ).delete(synchronize_session="fetch")
            for spec in object_specs:
                name = (spec.get("name") or "").strip()
                if not name or name == "其他" or name in preserved_object_names:
                    continue
                module_scores: dict[str, int] = {}
                for class_name in spec.get("source_classes", []):
                    for module_name in class_modules.get(class_name, set()):
                        module_scores[module_name] = module_scores.get(module_name, 0) + 2
                for table_name in spec.get("source_tables", []):
                    for module_name in table_modules.get(str(table_name).lower(), set()):
                        module_scores[module_name] = module_scores.get(module_name, 0) + 1
                best_module = max(module_scores, key=module_scores.get) if module_scores else None
                s.add(models.BusinessObject(
                    scan_id=scan.id,
                    domain_id=domain_map.get(best_module),
                    name=name,
                    description=spec.get("description"),
                    source_tables=spec.get("source_tables", []),
                    source_classes=spec.get("source_classes", []),
                    actions=spec.get("actions", []),
                    confidence=65,
                ))
                n_objects += 1
        else:
            n_objects = s.execute(
                select(func.count(models.BusinessObject.id)).where(
                    models.BusinessObject.scan_id == scan.id
                )
            ).scalar() or 0

        n_flows = 0
        if flow_specs is not None:
            preserved_entries = set(s.execute(
                select(models.BusinessFlow.entry_method_id).where(
                    models.BusinessFlow.scan_id == scan.id,
                    models.BusinessFlow.confirmed.is_(True),
                    models.BusinessFlow.entry_method_id.isnot(None),
                )
            ).scalars().all())
            s.query(models.BusinessFlow).filter(
                models.BusinessFlow.scan_id == scan.id,
                models.BusinessFlow.confirmed.is_not(True),
            ).delete(synchronize_session="fetch")
            for spec in flow_specs:
                entry = spec["entry"]
                if entry.id in preserved_entries:
                    continue
                s.add(models.BusinessFlow(
                    scan_id=scan.id,
                    name=spec.get("name") or entry.name,
                    description=spec.get("description"),
                    entry_method_id=entry.id,
                    entry_name=f"{entry.file_path}:{entry.start_line}",
                    business_steps=spec.get("business_steps", []),
                    tech_steps=spec.get("tech_steps", []),
                    confidence=60,
                ))
                n_flows += 1
        else:
            n_flows = s.execute(
                select(func.count(models.BusinessFlow.id)).where(
                    models.BusinessFlow.scan_id == scan.id
                )
            ).scalar() or 0

        n_rules = n_skipped = n_failed = 0
        for method, card, error in results:
            if error or card is None:
                n_failed += 1
                s.add(models.Question(
                    scan_id=scan.id,
                    qtype="llm_error",
                    desc=f"方法 {method.fq_name} 标注失败: {error or '空输出'}",
                ))
                continue
            if not card.get("is_business_rule", False):
                n_skipped += 1
                continue
            candidates = []
            if method.file_path.endswith(".java"):
                candidates = rules_mod.extract_candidates(
                    root / method.file_path,
                    {"start_line": method.start_line, "end_line": method.end_line},
                )
            confidence = dom_mod.compute_confidence(
                card, candidates, method.entry_kind
            )
            logic = card.get("logic", [])
            if isinstance(logic, list):
                logic = "\n".join(str(step) for step in logic)
            s.add(models.BusinessRule(
                scan_id=scan.id,
                domain_id=domain_map.get(module_name_by_id.get(method.module_id)),
                source_method_id=method.id,
                module=module_name_by_id.get(method.module_id),
                rule_type=_map_rule_type(card.get("rule_type")),
                name=card.get("name"),
                trigger=card.get("trigger"),
                pre_condition=card.get("pre_condition"),
                logic=logic,
                status_change=card.get("status_change"),
                data_change=card.get("data_change"),
                follow_up=card.get("follow_up"),
                exception=card.get("exception"),
                impact=card.get("impact"),
                evidence_file=method.file_path,
                evidence_lines=[method.start_line, method.end_line],
                evidence_snippet=rules_mod.read_snippet(
                    root / method.file_path, method.start_line, method.end_line
                )[:2000],
                confidence=confidence,
                state=models.RuleState.AI_LABELED,
                model=client.model,
                raw=card,
            ))
            n_rules += 1

        s.flush()
        n_chain = 0
        try:
            with s.begin_nested():
                new_chain = chain_mod.run_chain(s, scan.id, client)
                if new_chain is not None:
                    s.flush()
                    s.query(models.BusinessChain).filter(
                        models.BusinessChain.scan_id == scan.id,
                        models.BusinessChain.id != new_chain.id,
                    ).delete(synchronize_session=False)
                    n_chain = 1
                elif object_specs is not None:
                    s.query(models.BusinessChain).filter(
                        models.BusinessChain.scan_id == scan.id
                    ).delete(synchronize_session=False)
        except Exception as exc:
            warnings.append(f"业务推演失败，保留原结果: {exc}")

        s.commit()
    except Exception:
        s.rollback()
        raise

    return {
        "project_id": project_id, "scan_id": scan.id,
        "domains": len(domain_specs), "candidates": len(methods),
        "rules": n_rules, "skipped": n_skipped, "failed": n_failed,
        "objects": n_objects, "flows": n_flows, "chain": n_chain,
        "preserved_human_rules": preserved_human_rule_count,
        "warnings": warnings,
        "usage": usage_summary(),
    }
