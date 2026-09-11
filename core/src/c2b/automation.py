"""一键自动化编排（v0.4）：扫描 → AI 标注 → 模块需求规格。

顺序推进、每阶段独立短会话，供 Web API 后台线程与 CLI ``c2b pipeline``
复用。止停边界遵循产品可信口径：自动化产出可核对的草稿知识，正式交付
仍以人工确认为边界；未配置 AI 时只完成扫描并明确告知缺口。
"""
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


def ai_ready() -> bool:
    """当前设置是否足以发起 LLM 调用。

    mock 模式视为就绪；api 模式要求已配置 Key（本地推理服务在允许私网
    地址时通常免鉴权，与 LLMClient 的放宽口径一致）。
    """
    from . import settings_store
    st = settings_store.load_settings()
    if st.get("llm_mode") == "mock":
        return True
    if os.environ.get("C2B_ALLOW_PRIVATE_LLM", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return True
    return bool(st.get("llm_api_key"))


def _latest_done_scan(s: Session, project_id: int) -> models.Scan | None:
    return s.execute(
        select(models.Scan).where(
            models.Scan.project_id == project_id,
            models.Scan.status == models.ScanStatus.DONE,
        ).order_by(models.Scan.id.desc()).limit(1)
    ).scalar_one_or_none()


def run_auto(project_id: int, *, run_ai: bool = True, label_limit: int = 200,
             concurrency: int = 8,
             progress_cb=None) -> dict:
    """对一个项目顺序执行 扫描（如需）→ AI 标注 → 模块需求规格。

    progress_cb(stage: str, done: int | None, total: int | None) 中 stage
    使用 ``scan``/``scan:<sub>``/``label``/``label:<sub>``/``modules``；
    调用方（Web 任务或 CLI）自行决定如何展示。返回阶段结果摘要，任何
    阶段失败抛异常由调用方落为任务错误。
    """
    from .db import SessionLocal
    from .scanner.service import scan_project

    def report(stage: str, done=None, total=None):
        if progress_cb:
            progress_cb(stage, done, total)

    result: dict = {"scanned": False, "ai_ran": False, "notes": []}

    # ---------- 阶段 1：确定性扫描（已完成则跳过） ----------
    s = SessionLocal()
    try:
        project = s.get(models.Project, project_id)
        if project is None:
            raise ValueError(f"项目 {project_id} 不存在")
        scan = _latest_done_scan(s, project_id)
        if scan is None:
            root = Path(project.repo_path)
            if not root.is_dir() or not any(root.iterdir()):
                raise ValueError(
                    "代码目录不可用（仍在导入、导入失败或为空），无法自动扫描")
            report("scan")
            scan_project(
                s, root, project.name, project_id=project_id,
                progress=lambda stage, done, total:
                    report(f"scan:{stage}", done, total),
            )
            scan = _latest_done_scan(s, project_id)
            if scan is None:
                raise ValueError("扫描未完成，自动流程中止")
            result["scanned"] = True
    finally:
        s.close()

    if not run_ai:
        result["notes"].append("按本次设置跳过 AI 标注；图谱已就绪，可人工确认或补跑标注。")
        return result

    # ---------- 阶段 2：AI 语义标注 ----------
    if not ai_ready():
        result["notes"].append(
            "尚未配置 AI 服务（LLM API Key）。已完成的确定性图谱可直接查看；"
            "在工作台「LLM 设置」配置后再次一键分析即可继续自动标注。")
        return result

    from .semantics.run import run_label
    s2 = SessionLocal()
    try:
        report("label", 0, 0)

        def label_cb(done: int, total: int, stage: str):
            report(f"label:{stage}", done, total)

        result["label"] = run_label(s2, project_id, limit=label_limit,
                                    concurrency=concurrency,
                                    progress_cb=label_cb)
        result["ai_ran"] = True
    finally:
        s2.close()

    # ---------- 阶段 3：模块需求规格（单模块失败不阻断整体） ----------
    from .llm import LLMClient
    from .semantics.module_analysis import analyze_module
    s3 = SessionLocal()
    try:
        scan = _latest_done_scan(s3, project_id)
        if scan is None:
            raise ValueError("扫描记录不可用，无法进行模块分析")
        mods = list(s3.execute(
            select(models.Module).where(models.Module.scan_id == scan.id)
            .order_by(models.Module.lines.desc())
        ).scalars().all())
        analyzed_ids = set(s3.execute(
            select(models.ModuleAnalysis.module_id).where(
                models.ModuleAnalysis.scan_id == scan.id)
        ).scalars().all())
        pending = [m for m in mods if m.id not in analyzed_ids]
        total = len(mods)
        done = total - len(pending)
        report("modules", done, total)
        client = LLMClient()
        failed = 0
        errors: list[str] = []
        for m in pending:
            ms = SessionLocal()
            try:
                analyze_module(ms, scan.id, m.id, client)
            except Exception as e:
                failed += 1
                if len(errors) < 10:
                    errors.append(f"模块 {m.name} 分析失败: {e}")
            finally:
                ms.close()
            done += 1
            report("modules", done, total)
        result["modules"] = {"total": total, "analyzed": done - failed,
                             "failed": failed}
        if errors:
            result["notes"].extend(errors)
        if failed and done - failed == 0:
            raise RuntimeError(f"全部 {failed} 个模块分析失败")
    finally:
        s3.close()
    return result
