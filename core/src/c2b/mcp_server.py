"""MCP 服务器：把 c2b 能力暴露为标准 MCP 工具（stdio 传输）。

让支持 MCP 的 AI 客户端（ZCode、Claude Desktop、Codex 等）无需学习 CLI
即可调用 Code2Business：确定性扫描、流程状态、项目概览、规则清单、交付
文档（含客户交付总报告），以及 Agent Native 语义的证据导出/导入——AI
可以自己读代码完成语义标注再导入落库。

启动：``c2b mcp``（stdio）。数据库默认 ``~/.c2b/c2b.db``（与客户端启动
目录解耦）；用 ``C2B_DB_URL`` 指向与 Web 工作台相同的 PostgreSQL，AI 的
分析结果就会直接出现在工作台里。

长耗时提示：XL 级仓库的全量扫描更适合 Desktop/Web 的后台任务；MCP 场景
可先用 ``probe_only`` 秒级探测，或用 ``max_files`` 限制解析规模。
"""
import json
import os
from pathlib import Path

from . import models


def _ensure_default_db() -> None:
    """MCP 是常驻服务，数据库不能跟随 AI 客户端的 cwd 漂移。"""
    os.environ.setdefault(
        "C2B_DB_URL", f"sqlite:///{Path.home() / '.c2b' / 'c2b.db'}")


def _session():
    from .db import SessionLocal
    return SessionLocal()


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _project_or_raise(s, project_id: int) -> models.Project:
    project = s.get(models.Project, project_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在；先用 list_projects 查看已有项目")
    return project


def _scan_id_or_raise(s, project_id: int) -> int:
    scan = s.execute(
        select_done_scan(project_id)
    ).scalar_one_or_none()
    if scan is None:
        raise ValueError(f"项目 {project_id} 还没有已完成的扫描；先调用 scan")
    return scan.id


def select_done_scan(project_id: int):
    from sqlalchemy import select
    return (
        select(models.Scan).where(
            models.Scan.project_id == project_id,
            models.Scan.status == models.ScanStatus.DONE,
        ).order_by(models.Scan.id.desc()).limit(1)
    )


# ---------------- 工具实现（纯函数 + 独立短会话，便于直接测试） ----------------

def tool_list_projects() -> str:
    from sqlalchemy import select
    s = _session()
    try:
        rows = s.execute(
            select(models.Project).order_by(models.Project.id.desc())
        ).scalars().all()
        return _dumps([
            {"id": p.id, "name": p.name, "created_at": str(p.created_at)}
            for p in rows
        ])
    finally:
        s.close()


def tool_scan(code_root: str, name: str | None = None,
              max_files: int | None = None, probe_only: bool = False) -> str:
    """确定性扫描：探测 → 分片 → 解析 → 图谱落库。"""
    from .db import init_db
    from .scanner.graph import Scanner
    init_db()
    try:
        root = Path(code_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"代码目录不可用: {code_root}（{exc}）") from exc
    if not root.is_dir():
        raise ValueError(f"不是目录: {root}")
    s = _session()
    sc = None
    try:
        sc = Scanner(s, root, name or root.name, max_files=max_files)
        sc.create()
        report = sc.run_probe()
        if probe_only:
            return _dumps({
                "project_id": sc.project.id, "scan_id": sc.scan.id,
                "status": sc.scan.status.value, "scale": report,
                "note": "probe-only：仅规模探测，未生成图谱；再次调用 scan（同参数）即可正式扫描",
            })
        plan = sc.shard_plan(report)
        sc.run_parse(report, shards=plan["shards"] if plan["enabled"] else None)
        sc.finish()
        return _dumps({
            "project_id": sc.project.id, "scan_id": sc.scan.id,
            "status": sc.scan.status.value, "tier": report["tier"],
            "stats": sc.scan.stats,
        })
    except Exception as exc:
        if sc is not None:
            try:
                sc.fail(str(exc))
            except Exception:
                pass
        raise
    finally:
        s.close()


def tool_status(project_id: int) -> str:
    from .api.routes import pipeline
    from fastapi import HTTPException
    s = _session()
    try:
        _project_or_raise(s, project_id)
        try:
            flow = pipeline(project_id, s)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
        return _dumps(flow)
    finally:
        s.close()


def tool_overview(project_id: int) -> str:
    from .api.routes import overview
    from fastapi import HTTPException
    s = _session()
    try:
        _project_or_raise(s, project_id)
        try:
            data = overview(project_id, s)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
        return _dumps(data)
    finally:
        s.close()


def tool_rules(project_id: int, rule_type: str | None = None,
               state: str | None = None, min_confidence: int | None = None,
               limit: int = 100) -> str:
    from .api.routes import rules as rules_fn
    from fastapi import HTTPException
    s = _session()
    try:
        _project_or_raise(s, project_id)
        kwargs = {"limit": max(1, min(int(limit), 2000))}
        if rule_type:
            try:
                kwargs["rule_type"] = models.RuleType(rule_type)
            except ValueError as exc:
                raise ValueError(
                    f"rule_type 无效: {rule_type}；"
                    f"可选 {[t.value for t in models.RuleType]}") from exc
        if state:
            try:
                kwargs["state"] = models.RuleState(state)
            except ValueError as exc:
                raise ValueError(
                    f"state 无效: {state}；"
                    f"可选 {[t.value for t in models.RuleState]}") from exc
        if min_confidence is not None:
            kwargs["min_confidence"] = int(min_confidence)
        # 直调路由函数时 FastAPI 不会替换 Query() 默认值，必须显式传 None
        kwargs.setdefault("min_confidence", None)
        try:
            rows = rules_fn(project_id, s=s, **kwargs)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
        return _dumps({"count": len(rows), "rules": rows})
    finally:
        s.close()


def tool_document(project_id: int, doc_type: str = "combined",
                  draft: bool = False) -> str:
    """渲染交付文档 Markdown（combined=客户交付总报告）。"""
    from .documents import DOC_TYPES, _gather, render
    if doc_type not in DOC_TYPES:
        raise ValueError(f"未知文档类型 {doc_type}；可选 {list(DOC_TYPES)}")
    s = _session()
    try:
        scan_id = _scan_id_or_raise(s, project_id)
        d = _gather(s, scan_id, include_unconfirmed=draft)
        markdown = render(doc_type, d)
        title = DOC_TYPES[doc_type][0]
        return _dumps({"project_id": project_id, "doc_type": doc_type,
                       "title": title, "draft": draft,
                       "file_name": f"{title}{'（草稿）' if draft else ''}.md",
                       "markdown": markdown})
    finally:
        s.close()


def tool_export_evidence(project_id: int, max_methods: int = 500) -> str:
    """导出确定性图谱证据，供 AI 阅读源码后填写语义结果。"""
    from .semantics.agent_native import AgentSemanticError, export_evidence
    s = _session()
    try:
        _project_or_raise(s, project_id)
        try:
            evidence = export_evidence(s, project_id, max_methods=max_methods)
        except AgentSemanticError as exc:
            raise ValueError(str(exc)) from exc
        return _dumps(evidence)
    finally:
        s.close()


def tool_import_semantic(project_id: int, payload) -> str:
    """校验并导入 AI 产出的语义 JSON（domains/rules/objects/flows/chain）。"""
    from .semantics.agent_native import AgentSemanticError, import_semantic
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"payload 不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")
    s = _session()
    try:
        _project_or_raise(s, project_id)
        try:
            result = import_semantic(s, project_id, payload)
        except AgentSemanticError as exc:
            raise ValueError(f"未导入：{exc}") from exc
        return _dumps(result)
    finally:
        s.close()


# ---------------- MCP 服务组装 ----------------

def build_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    def as_tool_error(fn):
        """把 ValueError 转为 MCP 标准工具错误，客户端拿到可读原因。"""
        import functools

        @functools.wraps(fn)
        def wrapped(**kwargs):
            try:
                return fn(**kwargs)
            except ValueError as exc:
                raise ToolError(str(exc)) from None
        return wrapped

    server = MCPServer(
        "c2b",
        version=__import__("c2b").__version__,
        instructions=(
            "Code2Business：把遗留系统源代码还原为可核对的业务知识"
            "（业务域/业务对象/规则卡片/业务流程/客户交付总报告）。"
            "典型用法：scan 扫描代码目录 → export_evidence 导出证据 →"
            "（AI 阅读源码后）import_semantic 导入语义 → document 生成"
            "combined 客户交付总报告。所有结论都带代码证据与可信度。"
        ),
    )
    for name, fn in (
        ("list_projects", tool_list_projects),
        ("scan", tool_scan),
        ("status", tool_status),
        ("overview", tool_overview),
        ("rules", tool_rules),
        ("document", tool_document),
        ("export_evidence", tool_export_evidence),
        ("import_semantic", tool_import_semantic),
    ):
        server.tool(name=name)(as_tool_error(fn))
    return server


def serve() -> None:
    """以 stdio 传输运行 MCP 服务器（供 `c2b mcp` 调用）。"""
    _ensure_default_db()
    # cli 模块级 import 已经按 cwd 默认值建好了引擎；在第一个连接建立前
    # 用最终解析出的 DB URL 重建，避免把库写到 AI 客户端的工作目录。
    from . import config, db
    if config.DB_URL != os.environ.get("C2B_DB_URL"):
        db.reset_engine(os.environ["C2B_DB_URL"])
    from .db import init_db
    init_db()
    build_server().run(transport="stdio")
