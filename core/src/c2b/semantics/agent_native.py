"""Bridge the current coding Agent into C2B's reviewed semantic graph.

This module deliberately has no model client.  A Skill exports deterministic
scan evidence, lets the current Agent reason over the repository, then imports
only a validated JSON result.  This keeps Agent-native work auditable and lets
the normal Web, confirmation and document flows consume it unchanged.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .run import _map_rule_type

CONTRACT_VERSION = "1.0"
AGENT_MODEL = "agent-native"

# This is an intentionally compact template rather than a free-form prompt.
# `agent-schema` exposes it so a Skill and a human Agent share one contract.
AGENT_SEMANTIC_TEMPLATE: dict[str, Any] = {
    "contract_version": CONTRACT_VERSION,
    "domains": [{"name": "订单管理", "description": "…", "modules": ["orders"], "confidence": 75}],
    "objects": [{"name": "订单", "domain": "订单管理", "description": "…", "source_tables": ["orders"], "source_classes": ["Order"], "actions": ["创建", "审批"], "confidence": 70}],
    "rules": [{
        "name": "订单审批前校验", "domain": "订单管理", "rule_type": "validation",
        "trigger": "调用审批接口", "pre_condition": "订单存在", "logic": "校验通过后更新状态",
        "status_change": "待审批 → 已审批", "data_change": "更新订单状态", "follow_up": "发送通知",
        "exception": "校验失败时拒绝审批", "impact": "订单审批", "confidence": 80,
        "source_method": "OrderService.approve()",
        "evidence": {"file": "src/OrderService.java", "lines": [42, 60], "snippet": "…"},
    }],
    "flows": [{"name": "订单审批", "description": "…", "entry_method": "OrderController.approve()", "business_steps": ["提交审批", "校验订单", "更新状态"], "tech_steps": ["Controller", "Service", "Repository"], "confidence": 75}],
    "chain": {"mainline": ["订单", "审批", "通知"], "relationships": [], "design_narrative": "…", "assumptions": [], "confidence": 60},
    "questions": [{"qtype": "agent_native", "desc": "…", "suggestion": "请业务方确认", "status": "open"}],
}


class AgentSemanticError(ValueError):
    """A semantic file is incomplete, inconsistent, or not grounded in scan evidence."""


def _latest_done_scan(s: Session, project_id: int) -> models.Scan:
    project = s.get(models.Project, project_id)
    if project is None:
        raise AgentSemanticError(f"项目 {project_id} 不存在")
    scan = s.execute(
        select(models.Scan).where(
            models.Scan.project_id == project_id,
            models.Scan.status == models.ScanStatus.DONE,
        ).order_by(models.Scan.id.desc()).limit(1)
    ).scalar_one_or_none()
    if scan is None:
        raise AgentSemanticError("项目还没有已完成的扫描，先执行 c2b scan")
    return scan


def _string(value: Any, field: str, *, required: bool = False,
            max_length: int = 20_000) -> str:
    if value is None:
        if required:
            raise AgentSemanticError(f"{field} 为必填字符串")
        return ""
    if not isinstance(value, str):
        raise AgentSemanticError(f"{field} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise AgentSemanticError(f"{field} 不能为空")
    if len(value) > max_length:
        raise AgentSemanticError(f"{field} 超过最大长度 {max_length}")
    return value


def _string_list(value: Any, field: str, *, max_items: int = 500) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise AgentSemanticError(f"{field} 必须是不超过 {max_items} 项的数组")
    return [_string(item, f"{field}[]", required=True, max_length=2000) for item in value]


def _confidence(value: Any, field: str = "confidence") -> int:
    if value is None:
        return 60
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise AgentSemanticError(f"{field} 必须是 0 到 100 的整数")
    return value


def _top_level(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentSemanticError("语义结果根节点必须是 JSON 对象")
    expected = {"contract_version", "domains", "objects", "rules", "flows", "chain", "questions"}
    missing = expected - set(payload)
    extra = set(payload) - expected
    if missing:
        raise AgentSemanticError("语义结果缺少字段: " + ", ".join(sorted(missing)))
    if extra:
        raise AgentSemanticError("语义结果包含未知字段: " + ", ".join(sorted(extra)))
    if payload["contract_version"] != CONTRACT_VERSION:
        raise AgentSemanticError(
            f"不支持的 contract_version：{payload['contract_version']!r}，需要 {CONTRACT_VERSION}"
        )
    for key in ("domains", "objects", "rules", "flows", "questions"):
        if not isinstance(payload[key], list):
            raise AgentSemanticError(f"{key} 必须是数组")
    if payload["chain"] is not None and not isinstance(payload["chain"], dict):
        raise AgentSemanticError("chain 必须是对象或 null")
    return payload


def export_evidence(s: Session, project_id: int, *, max_methods: int = 500) -> dict[str, Any]:
    """Export deterministic graph evidence for an Agent; no source code or secrets leave C2B."""
    if not 1 <= max_methods <= 10_000:
        raise AgentSemanticError("max_methods 必须在 1 到 10000 之间")
    scan = _latest_done_scan(s, project_id)
    project = s.get(models.Project, project_id)
    modules = s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
        .order_by(models.Module.lines.desc())
    ).scalars().all()
    methods = s.execute(
        select(models.CodeObject, models.Module.name)
        .join(models.Module, models.Module.id == models.CodeObject.module_id, isouter=True)
        .where(models.CodeObject.scan_id == scan.id,
               models.CodeObject.obj_type == models.ObjType.METHOD)
        .order_by(models.CodeObject.is_entry.desc(), models.CodeObject.file_path,
                  models.CodeObject.start_line)
        .limit(max_methods)
    ).all()
    endpoints = s.execute(
        select(models.ApiEndpoint, models.CodeObject.fq_name)
        .join(models.CodeObject, models.CodeObject.id == models.ApiEndpoint.controller_obj_id,
              isouter=True)
        .where(models.ApiEndpoint.scan_id == scan.id)
        .order_by(models.ApiEndpoint.path)
    ).all()
    tables = s.execute(
        select(models.DataTable).where(models.DataTable.scan_id == scan.id)
        .order_by(models.DataTable.name)
    ).scalars().all()
    return {
        "contract_version": CONTRACT_VERSION,
        "project": {"id": project.id, "name": project.name},
        "scan": {"id": scan.id, "stats": scan.stats or {}},
        "modules": [{"name": m.name, "lines": m.lines, "files": m.file_count}
                    for m in modules],
        "methods": [{
            "fq_name": obj.fq_name, "name": obj.name, "module": module,
            "file": obj.file_path, "lines": [obj.start_line, obj.end_line],
            "entry": obj.is_entry, "entry_kind": obj.entry_kind,
            "signature": obj.signature, "comment": obj.comment,
        } for obj, module in methods],
        "endpoints": [{"method": e.method, "path": e.path, "controller": fq,
                       "description": e.description, "auth": e.auth}
                      for e, fq in endpoints],
        "tables": [{"name": table.name, "comment": table.comment, "source": table.source}
                   for table in tables],
        "instructions": "请阅读项目源码并按 c2b agent-schema 输出语义结果。每条规则必须引用 methods 中存在且覆盖证据行的文件与行号；不要编造无法在代码中验证的结论。",
    }


def import_semantic(s: Session, project_id: int, payload: Any) -> dict[str, int]:
    """Validate and atomically materialize an Agent-native semantic result."""
    data = _top_level(payload)
    scan = _latest_done_scan(s, project_id)
    modules = {name for name in s.execute(
        select(models.Module.name).where(models.Module.scan_id == scan.id)
    ).scalars().all()}
    code_objects = s.execute(
        select(models.CodeObject).where(models.CodeObject.scan_id == scan.id)
    ).scalars().all()
    methods_by_fq = {obj.fq_name: obj for obj in code_objects if obj.fq_name}
    methods_by_file: dict[str, list[models.CodeObject]] = {}
    for obj in code_objects:
        if obj.obj_type == models.ObjType.METHOD:
            methods_by_file.setdefault(obj.file_path, []).append(obj)
    module_by_id = {m.id: m.name for m in s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
    ).scalars().all()}

    domain_specs: list[dict[str, Any]] = []
    domain_names: set[str] = set()
    for index, item in enumerate(data["domains"]):
        if not isinstance(item, dict):
            raise AgentSemanticError(f"domains[{index}] 必须是对象")
        name = _string(item.get("name"), f"domains[{index}].name", required=True, max_length=300)
        if name in domain_names:
            raise AgentSemanticError(f"业务域重复: {name}")
        domain_names.add(name)
        item_modules = _string_list(item.get("modules"), f"domains[{index}].modules")
        unknown_modules = set(item_modules) - modules
        if unknown_modules:
            raise AgentSemanticError("业务域引用了未知模块: " + ", ".join(sorted(unknown_modules)))
        domain_specs.append({"name": name, "description": _string(item.get("description"), f"domains[{index}].description"), "modules": item_modules, "confidence": _confidence(item.get("confidence"), f"domains[{index}].confidence")})

    def domain_id(name: Any, field: str, mapping: dict[str, int]) -> int | None:
        value = _string(name, field)
        if not value:
            return None
        if value not in mapping:
            raise AgentSemanticError(f"{field} 引用了未知业务域: {value}")
        return mapping[value]

    rule_specs: list[dict[str, Any]] = []
    for index, item in enumerate(data["rules"]):
        if not isinstance(item, dict):
            raise AgentSemanticError(f"rules[{index}] 必须是对象")
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            raise AgentSemanticError(f"rules[{index}].evidence 必须是对象")
        file_path = _string(evidence.get("file"), f"rules[{index}].evidence.file", required=True, max_length=1024)
        lines = evidence.get("lines")
        if (not isinstance(lines, list) or len(lines) != 2 or
                not all(isinstance(line, int) and not isinstance(line, bool) for line in lines) or
                lines[0] < 1 or lines[1] < lines[0]):
            raise AgentSemanticError(f"rules[{index}].evidence.lines 必须是有效的 [起始行, 结束行]")
        candidates = [method for method in methods_by_file.get(file_path, [])
                      if method.start_line is not None and method.end_line is not None and
                      method.start_line <= lines[0] <= lines[1] <= method.end_line]
        if not candidates:
            raise AgentSemanticError(f"rules[{index}] 的证据不在本次扫描的方法范围内: {file_path}:{lines[0]}-{lines[1]}")
        source_method = _string(item.get("source_method"), f"rules[{index}].source_method")
        if source_method:
            method = methods_by_fq.get(source_method)
            if method is None:
                raise AgentSemanticError(f"rules[{index}].source_method 不存在于本次扫描: {source_method}")
            if method not in candidates:
                raise AgentSemanticError(f"rules[{index}] 的 source_method 与 evidence 不一致")
        else:
            method = candidates[0]
        rule_specs.append({
            "domain": _string(item.get("domain"), f"rules[{index}].domain"),
            "source_method": method,
            "module": module_by_id.get(method.module_id),
            "rule_type": _map_rule_type(_string(item.get("rule_type"), f"rules[{index}].rule_type")),
            "name": _string(item.get("name"), f"rules[{index}].name", required=True, max_length=500),
            "trigger": _string(item.get("trigger"), f"rules[{index}].trigger", max_length=500),
            "pre_condition": _string(item.get("pre_condition"), f"rules[{index}].pre_condition"),
            "logic": _string(item.get("logic"), f"rules[{index}].logic", required=True),
            "status_change": _string(item.get("status_change"), f"rules[{index}].status_change"),
            "data_change": _string(item.get("data_change"), f"rules[{index}].data_change"),
            "follow_up": _string(item.get("follow_up"), f"rules[{index}].follow_up"),
            "exception": _string(item.get("exception"), f"rules[{index}].exception"),
            "impact": _string(item.get("impact"), f"rules[{index}].impact"),
            "evidence_file": file_path, "evidence_lines": lines,
            "evidence_snippet": _string(evidence.get("snippet"), f"rules[{index}].evidence.snippet", max_length=4000),
            "confidence": _confidence(item.get("confidence"), f"rules[{index}].confidence"),
            "raw": item,
        })

    object_specs: list[dict[str, Any]] = []
    for index, item in enumerate(data["objects"]):
        if not isinstance(item, dict):
            raise AgentSemanticError(f"objects[{index}] 必须是对象")
        object_specs.append({"name": _string(item.get("name"), f"objects[{index}].name", required=True, max_length=300), "domain": _string(item.get("domain"), f"objects[{index}].domain"), "description": _string(item.get("description"), f"objects[{index}].description"), "source_tables": _string_list(item.get("source_tables"), f"objects[{index}].source_tables"), "source_classes": _string_list(item.get("source_classes"), f"objects[{index}].source_classes"), "actions": _string_list(item.get("actions"), f"objects[{index}].actions"), "confidence": _confidence(item.get("confidence"), f"objects[{index}].confidence")})

    flow_specs: list[dict[str, Any]] = []
    for index, item in enumerate(data["flows"]):
        if not isinstance(item, dict):
            raise AgentSemanticError(f"flows[{index}] 必须是对象")
        entry = _string(item.get("entry_method"), f"flows[{index}].entry_method")
        if entry and entry not in methods_by_fq:
            raise AgentSemanticError(f"flows[{index}].entry_method 不存在于本次扫描: {entry}")
        flow_specs.append({"name": _string(item.get("name"), f"flows[{index}].name", required=True, max_length=300), "description": _string(item.get("description"), f"flows[{index}].description"), "entry": methods_by_fq.get(entry), "entry_name": entry, "business_steps": _string_list(item.get("business_steps"), f"flows[{index}].business_steps"), "tech_steps": _string_list(item.get("tech_steps"), f"flows[{index}].tech_steps"), "confidence": _confidence(item.get("confidence"), f"flows[{index}].confidence")})

    chain = data["chain"]
    if chain is not None:
        for key in ("mainline", "relationships", "assumptions"):
            if not isinstance(chain.get(key), list):
                raise AgentSemanticError(f"chain.{key} 必须是数组")
        if not all(isinstance(item, dict) for item in chain["relationships"]):
            raise AgentSemanticError("chain.relationships 的每一项必须是对象")
        chain_spec = {"mainline": _string_list(chain["mainline"], "chain.mainline"), "relationships": chain["relationships"], "design_narrative": _string(chain.get("design_narrative"), "chain.design_narrative"), "assumptions": _string_list(chain["assumptions"], "chain.assumptions"), "confidence": _confidence(chain.get("confidence"), "chain.confidence")}
    else:
        chain_spec = None

    question_specs: list[dict[str, str]] = []
    for index, item in enumerate(data["questions"]):
        if not isinstance(item, dict):
            raise AgentSemanticError(f"questions[{index}] 必须是对象")
        status = _string(item.get("status"), f"questions[{index}].status") or "open"
        if status not in {"open", "resolved"}:
            raise AgentSemanticError(f"questions[{index}].status 只能是 open 或 resolved")
        question_specs.append({"qtype": _string(item.get("qtype"), f"questions[{index}].qtype", max_length=30) or "agent_native", "desc": _string(item.get("desc"), f"questions[{index}].desc", required=True), "suggestion": _string(item.get("suggestion"), f"questions[{index}].suggestion"), "status": status})

    try:
        generated_rule_ids = list(s.execute(select(models.BusinessRule.id).where(models.BusinessRule.scan_id == scan.id, models.BusinessRule.state.in_([models.RuleState.AI_LABELED, models.RuleState.CONFLICT]))).scalars())
        if generated_rule_ids:
            s.query(models.Question).filter(models.Question.rule_id.in_(generated_rule_ids)).delete(synchronize_session=False)
            s.query(models.BusinessRule).filter(
                models.BusinessRule.id.in_(generated_rule_ids)
            ).delete(synchronize_session="fetch")
        s.query(models.Question).filter(models.Question.scan_id == scan.id, models.Question.qtype == "agent_native").delete(synchronize_session=False)
        s.query(models.BusinessObject).filter(
            models.BusinessObject.scan_id == scan.id,
            models.BusinessObject.confirmed.is_not(True),
        ).delete(synchronize_session="fetch")
        s.query(models.BusinessFlow).filter(
            models.BusinessFlow.scan_id == scan.id,
            models.BusinessFlow.confirmed.is_not(True),
        ).delete(synchronize_session="fetch")
        s.query(models.BusinessChain).filter(models.BusinessChain.scan_id == scan.id, models.BusinessChain.model == AGENT_MODEL).delete(synchronize_session=False)

        existing = {domain.name: domain for domain in s.execute(select(models.BusinessDomain).where(models.BusinessDomain.scan_id == scan.id)).scalars()}
        for spec in domain_specs:
            domain = existing.get(spec["name"])
            if domain is None:
                domain = models.BusinessDomain(scan_id=scan.id, name=spec["name"])
                s.add(domain)
                existing[spec["name"]] = domain
            if not domain.confirmed:
                domain.description, domain.modules, domain.confidence = spec["description"], spec["modules"], spec["confidence"]
        s.flush()
        domain_map = {name: domain.id for name, domain in existing.items()}

        for spec in object_specs:
            s.add(models.BusinessObject(scan_id=scan.id, domain_id=domain_id(spec["domain"], "object.domain", domain_map), name=spec["name"], description=spec["description"], source_tables=spec["source_tables"], source_classes=spec["source_classes"], actions=spec["actions"], confidence=spec["confidence"]))
        for spec in rule_specs:
            s.add(models.BusinessRule(scan_id=scan.id, domain_id=domain_id(spec["domain"], "rule.domain", domain_map), source_method_id=spec["source_method"].id, module=spec["module"], rule_type=spec["rule_type"], name=spec["name"], trigger=spec["trigger"], pre_condition=spec["pre_condition"], logic=spec["logic"], status_change=spec["status_change"], data_change=spec["data_change"], follow_up=spec["follow_up"], exception=spec["exception"], impact=spec["impact"], evidence_file=spec["evidence_file"], evidence_lines=spec["evidence_lines"], evidence_snippet=spec["evidence_snippet"], confidence=spec["confidence"], state=models.RuleState.AI_LABELED, model=AGENT_MODEL, raw={"provider": AGENT_MODEL, "result": spec["raw"]}))
        for spec in flow_specs:
            s.add(models.BusinessFlow(scan_id=scan.id, name=spec["name"], description=spec["description"], entry_method_id=spec["entry"].id if spec["entry"] else None, entry_name=spec["entry_name"], business_steps=spec["business_steps"], tech_steps=spec["tech_steps"], confidence=spec["confidence"]))
        if chain_spec is not None:
            s.add(models.BusinessChain(scan_id=scan.id, model=AGENT_MODEL, **chain_spec))
        for spec in question_specs:
            s.add(models.Question(scan_id=scan.id, **spec))
        s.commit()
    except Exception:
        s.rollback()
        raise
    return {"project_id": project_id, "scan_id": scan.id, "domains": len(domain_specs), "objects": len(object_specs), "rules": len(rule_specs), "flows": len(flow_specs), "chain": int(chain_spec is not None), "questions": len(question_specs)}
