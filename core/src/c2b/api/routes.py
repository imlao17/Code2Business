"""FastAPI 查询 API（对齐 mvp-tech-solution.md 第 6 章核心端点）。"""
import os
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..db import get_session

router = APIRouter(prefix="/api")


class ConfirmIn(BaseModel):
    action: Literal["confirm", "modify", "discard"]
    user: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=4000)
    fields: dict | None = None   # modify 时提供


def _latest_scan(
    s: Session, project_id: int, *, completed_only: bool = True
) -> models.Scan:
    """Return the newest usable scan, preserving the last good result.

    Read APIs default to a completed scan.  A later failed or in-progress
    attempt must not make a previously generated graph disappear.
    """
    query = select(models.Scan).where(models.Scan.project_id == project_id)
    if completed_only:
        query = query.where(models.Scan.status == models.ScanStatus.DONE)
    scan = s.execute(
        query
        .order_by(models.Scan.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if scan is None:
        detail = "项目还没有已完成的扫描" if completed_only else "项目还没有扫描记录"
        raise HTTPException(404, detail)
    return scan


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _excel_safe(value):
    """Prevent spreadsheet clients from evaluating untrusted text as formulae."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _create_project_job(
    s: Session,
    project_id: int,
    *,
    limit: int,
    stage: str = "preparing",
) -> models.LabelJob:
    """Atomically claim the single background-job slot for a project."""
    job = models.LabelJob(
        project_id=project_id,
        status="running",
        limit=limit,
        stage=stage,
    )
    s.add(job)
    try:
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        running = s.execute(
            select(models.LabelJob.id).where(
                models.LabelJob.project_id == project_id,
                models.LabelJob.status == "running",
            ).limit(1)
        ).scalar_one_or_none()
        if running is not None:
            raise HTTPException(409, "项目已有运行中的任务") from exc
        raise
    return job


@router.post("/projects/import")
async def import_project(name: str = Form(...),
                         file: UploadFile | None = File(None),
                         git_url: str | None = Form(None),
                         s: Session = Depends(get_session)):
    """网页端创建项目：上传 zip 代码包或 Git 地址 → 自动扫描。

    流程：落盘 → 解压/克隆 → 创建项目 → 后台线程全流程扫描。
    """
    import shutil
    import subprocess
    import threading
    import zipfile
    from pathlib import Path as _Path
    from ..security import (
        UnsafeArchiveError,
        UnsafeUrlError,
        project_storage_path,
        safe_extract_zip,
        validate_outbound_url,
    )

    if not name.strip():
        raise HTTPException(400, "项目名称不能为空")
    if len(name.strip()) > 200:
        raise HTTPException(400, "项目名称不能超过 200 个字符")
    if bool(file) == bool(git_url and git_url.strip()):
        raise HTTPException(400, "请且仅请选择一种导入方式：zip 文件或 Git 地址")

    normalized_git_url = None
    if git_url:
        if len(git_url.strip()) > 2048:
            raise HTTPException(400, "Git 地址不能超过 2048 个字符")
        try:
            normalized_git_url = validate_outbound_url(
                git_url,
                purpose="git",
                allow_private=_env_flag("C2B_ALLOW_PRIVATE_GIT"),
            )
        except UnsafeUrlError as exc:
            raise HTTPException(400, f"Git 地址不安全: {exc}") from exc

    data_root = _Path(os.environ.get("C2B_DATA_ROOT", "./c2b-data"))
    proj_dir = project_storage_path(data_root)
    proj_dir.mkdir(parents=True, exist_ok=True)

    try:
        if file:
            # Stream to disk so a large upload does not consume process memory.
            zip_path = proj_dir / "upload.zip"
            max_upload = int(os.environ.get("C2B_MAX_UPLOAD_BYTES", str(1024**3)))
            uploaded = 0
            with zip_path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    uploaded += len(chunk)
                    if uploaded > max_upload:
                        raise HTTPException(413, f"上传文件超过限制（{max_upload:,} 字节）")
                    output.write(chunk)
            with zipfile.ZipFile(zip_path) as zf:
                safe_extract_zip(
                    zf,
                    proj_dir,
                    max_members=int(os.environ.get("C2B_MAX_ARCHIVE_FILES", "100000")),
                    max_uncompressed_bytes=int(
                        os.environ.get("C2B_MAX_ARCHIVE_BYTES", str(10 * 1024**3))
                    ),
                )
            zip_path.unlink()
            # 若 zip 内只有一层顶层目录，则提升该目录为项目根
            subdirs = [p for p in proj_dir.iterdir() if p.is_dir()]
            files = [p for p in proj_dir.iterdir() if p.is_file()]
            if len(subdirs) == 1 and not files:
                inner = subdirs[0]
                for item in inner.iterdir():
                    shutil.move(str(item), str(proj_dir / item.name))
                inner.rmdir()
    except (HTTPException, UnsafeArchiveError, zipfile.BadZipFile) as exc:
        shutil.rmtree(proj_dir, ignore_errors=True)
        if isinstance(exc, HTTPException):
            raise
        detail = "上传文件不是有效的 zip 压缩包" if isinstance(
            exc, zipfile.BadZipFile
        ) else str(exc)
        raise HTTPException(400, detail) from exc
    except Exception as e:
        shutil.rmtree(proj_dir, ignore_errors=True)
        raise HTTPException(500, f"代码导入失败: {e}")

    # 创建项目 + 异步扫描（git clone 也在后台执行，请求秒回）
    project = models.Project(name=name.strip(), repo_path=str(proj_dir))
    s.add(project)
    s.flush()
    job = models.LabelJob(project_id=project.id, status="running",
                          stage="scan", total=0)
    s.add(job)
    s.commit()
    job_id = job.id

    def _scan_worker(job_id: int, project_id: int, root: str, git_url: str | None):
        from ..db import SessionLocal
        from ..scanner.service import scan_project
        ws = SessionLocal()
        try:
            result = None

            def cb(stage: str, done: int, total: int):
                cs = SessionLocal()
                try:
                    j = cs.get(models.LabelJob, job_id)
                    if j:
                        j.stage = f"scan:{stage}"
                        j.progress = done
                        j.total = total
                        cs.commit()
                finally:
                    cs.close()

            if git_url:
                # Git 克隆（后台执行）
                cs = SessionLocal()
                try:
                    j = cs.get(models.LabelJob, job_id)
                    j.stage = "git-clone"
                    cs.commit()
                finally:
                    cs.close()
                r = subprocess.run(
                    [
                        "git", "-c", "protocol.file.allow=never",
                        "-c", "protocol.ext.allow=never",
                        "-c", "http.followRedirects=false",
                        "clone", "--depth", "1", "--", git_url, root,
                    ],
                    capture_output=True, text=True, timeout=1800)
                if r.returncode != 0:
                    shutil.rmtree(root, ignore_errors=True)
                    raise RuntimeError(f"Git 克隆失败: {r.stderr[:300]}")

            result = scan_project(ws, _Path(root), name.strip(),
                                  project_id=project_id,
                                  progress=cb)
            fs = SessionLocal()
            try:
                j = fs.get(models.LabelJob, job_id)
                j.status = "done"
                j.result = {"scan": "done", "stats": result["stats"],
                            "tier": result["tier"]}
                j.finished_at = models.utcnow()
                fs.commit()
            finally:
                fs.close()
        except Exception as e:
            fs = SessionLocal()
            try:
                j = fs.get(models.LabelJob, job_id)
                if j:
                    j.status = "failed"
                    j.error = str(e)[:2000]
                    j.finished_at = models.utcnow()
                    fs.commit()
            finally:
                fs.close()
        finally:
            ws.close()

    threading.Thread(target=_scan_worker,
                     args=(job_id, project.id, str(proj_dir), normalized_git_url),
                     daemon=True).start()
    return {"ok": True, "project_id": project.id, "job_id": job_id,
            "message": "项目已创建，扫描任务后台运行中"}


@router.delete("/projects/{pid}")
def delete_project(pid: int, delete_files: bool = True,
                   s: Session = Depends(get_session)):
    """删除项目（含全部图谱数据；delete_files=false 时保留磁盘代码目录）。"""
    import shutil
    from pathlib import Path as _Path

    project = s.get(models.Project, pid)
    if project is None:
        raise HTTPException(404, "项目不存在")
    running = s.execute(
        select(models.LabelJob.id).where(
            models.LabelJob.project_id == pid,
            models.LabelJob.status == "running",
        ).limit(1)
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(409, "项目仍有运行中的任务，请等待任务结束后再删除")
    scan_ids = [r[0] for r in s.execute(
        select(models.Scan.id).where(models.Scan.project_id == pid)).all()]

    # 按引用顺序清理（PG 强制外键）
    for sid in scan_ids:
        s.query(models.Confirmation).filter(
            models.Confirmation.scan_id == sid).delete(synchronize_session=False)
        s.query(models.Question).filter(
            models.Question.scan_id == sid).delete(synchronize_session=False)
        s.query(models.ModuleAnalysis).filter(
            models.ModuleAnalysis.scan_id == sid).delete(synchronize_session=False)
        s.query(models.BusinessRule).filter(
            models.BusinessRule.scan_id == sid).delete(synchronize_session=False)
        s.query(models.BusinessFlow).filter(
            models.BusinessFlow.scan_id == sid).delete(synchronize_session=False)
        s.query(models.BusinessChain).filter(
            models.BusinessChain.scan_id == sid).delete(synchronize_session=False)
        s.query(models.BusinessObject).filter(
            models.BusinessObject.scan_id == sid).delete(synchronize_session=False)
        s.query(models.BusinessDomain).filter(
            models.BusinessDomain.scan_id == sid).delete(synchronize_session=False)
        s.query(models.CallEdge).filter(
            models.CallEdge.scan_id == sid).delete(synchronize_session=False)
        s.query(models.SqlOperation).filter(
            models.SqlOperation.scan_id == sid).delete(synchronize_session=False)
        s.query(models.DataField).filter(
            models.DataField.table_id.in_(
                select(models.DataTable.id).where(
                    models.DataTable.scan_id == sid))).delete(
            synchronize_session=False)
        s.query(models.DataTable).filter(
            models.DataTable.scan_id == sid).delete(synchronize_session=False)
        s.query(models.ApiEndpoint).filter(
            models.ApiEndpoint.scan_id == sid).delete(synchronize_session=False)
        s.query(models.CodeObject).filter(
            models.CodeObject.scan_id == sid).delete(synchronize_session=False)
        s.query(models.Module).filter(
            models.Module.scan_id == sid).delete(synchronize_session=False)
        s.query(models.ScanTask).filter(
            models.ScanTask.scan_id == sid).delete(synchronize_session=False)
    s.query(models.Scan).filter(
        models.Scan.project_id == pid).delete(synchronize_session=False)
    s.query(models.QaRecord).filter(
        models.QaRecord.project_id == pid).delete(synchronize_session=False)
    s.query(models.LabelJob).filter(
        models.LabelJob.project_id == pid).delete(synchronize_session=False)
    s.delete(project)
    s.commit()

    files_deleted = False
    files_delete_skipped = False
    if delete_files and project.repo_path:
        p = _Path(project.repo_path)
        managed_root = (
            _Path(os.environ.get("C2B_DATA_ROOT", "./c2b-data")) / "projects"
        ).resolve()
        resolved = p.resolve()
        if p.exists() and resolved != managed_root and resolved.is_relative_to(managed_root):
            shutil.rmtree(p, ignore_errors=True)
            files_deleted = not p.exists()
            files_delete_skipped = p.exists()
        elif p.exists():
            # Never infer safety from a path substring.  Legacy/external paths
            # are intentionally retained and can be removed by their owner.
            files_delete_skipped = True
    return {"ok": True, "deleted_project": pid,
            "files_deleted": files_deleted,
            "files_delete_skipped": files_delete_skipped}


@router.get("/projects/{pid}/pipeline")
def pipeline(pid: int, s: Session = Depends(get_session)):
    """项目处理流程状态：导入→扫描→标注→图谱→确认→导出。"""
    project = s.get(models.Project, pid)
    if project is None:
        raise HTTPException(404, "项目不存在")
    scans = s.execute(
        select(models.Scan).where(models.Scan.project_id == pid)
        .order_by(models.Scan.id)).scalars().all()
    scan_done = any(sc.status == models.ScanStatus.DONE for sc in scans)
    jobs = s.execute(
        select(models.LabelJob).where(models.LabelJob.project_id == pid)
        .order_by(models.LabelJob.id.desc()).limit(5)).scalars().all()
    running_job = next((j for j in jobs if j.status == "running"), None)
    running_scan = running_job if (running_job and
                                   str(running_job.stage).startswith(("scan", "git"))) else None
    running_label = running_job if (
        running_job
        and not str(running_job.stage).startswith(("scan", "git", "modules"))
    ) else None

    completed = [sc for sc in scans if sc.status == models.ScanStatus.DONE]
    scan_id = completed[-1].id if completed else None
    n_rules = n_objects = n_flows = n_chain = n_confirm = 0
    if scan_id:
        n_rules = s.execute(select(func.count(models.BusinessRule.id))
                            .where(models.BusinessRule.scan_id == scan_id)).scalar() or 0
        n_objects = s.execute(select(func.count(models.BusinessObject.id))
                              .where(models.BusinessObject.scan_id == scan_id)).scalar() or 0
        n_flows = s.execute(select(func.count(models.BusinessFlow.id))
                            .where(models.BusinessFlow.scan_id == scan_id)).scalar() or 0
        n_chain = s.execute(select(func.count(models.BusinessChain.id))
                            .where(models.BusinessChain.scan_id == scan_id)).scalar() or 0
    if scan_id:
        n_confirm = s.execute(
            select(func.count(models.BusinessRule.id)).where(
                models.BusinessRule.scan_id == scan_id,
                models.BusinessRule.state.in_([
                    models.RuleState.CONFIRMED,
                    models.RuleState.MODIFIED,
                ]),
            )
        ).scalar() or 0

    def step(key, label, page, status, extra=None):
        return {"key": key, "label": label, "page": page,
                "status": status, **(extra or {})}

    steps = []
    steps.append(step("import", "导入代码", "/overview", "done"))
    if not scan_done:
        if running_scan:
            steps.append(step("scan", "扫描分析", "/overview", "running",
                              {"stage": running_scan.stage,
                               "progress": running_scan.progress,
                               "total": running_scan.total}))
        else:
            steps.append(step("scan", "扫描分析", "/overview", "pending"))
    else:
        steps.append(step("scan", "扫描分析", "/module-specs", "done"))
    if n_rules > 0:
        steps.append(step("label", "AI 标注", "/module-specs", "done",
                          {"count": n_rules}))
    elif running_label:
        steps.append(step("label", "AI 标注", "/module-specs", "running",
                          {"stage": running_label.stage,
                           "progress": running_label.progress,
                           "total": running_label.total}))
    else:
        steps.append(step("label", "AI 标注", "/module-specs", "pending"))
    if n_objects or n_flows or n_chain:
        steps.append(step("graph", "业务图谱", "/business", "done",
                          {"count": n_objects}))
    else:
        steps.append(step("graph", "业务图谱", "/business", "pending"))
    if n_confirm > 0:
        steps.append(step("confirm", "人工确认", "/rules", "done",
                          {"count": n_confirm}))
    else:
        steps.append(step("confirm", "人工确认", "/rules", "pending"))
    # 导出：前序完成后可操作
    steps.append(step("export", "导出文档", "/documents",
                      "ready" if n_rules else "pending"))

    # 当前应做步骤（第一个未完成的非导出步骤；全完成则导出）
    next_step = next((st for st in steps if st["status"] == "pending"
                      and st["key"] != "export"), None)
    if next_step is None and all(st["status"] in ("done", "ready")
                                 for st in steps if st["key"] != "export"):
        next_step = steps[-1]
    return {"project_id": pid, "steps": steps,
            "next": {"key": next_step["key"], "label": next_step["label"],
                     "page": next_step["page"],
                     "status": next_step["status"]} if next_step else None}


@router.get("/projects")
def list_projects(s: Session = Depends(get_session)):
    rows = s.execute(select(models.Project).order_by(models.Project.id.desc())).scalars().all()
    return [{"id": r.id, "name": r.name, "tech_stack": r.tech_stack,
             "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/projects/{pid}/overview")
def overview(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    m = scan.meta or {}
    st = scan.stats or {}
    rules_count = s.execute(
        select(func.count(models.BusinessRule.id))
        .where(models.BusinessRule.scan_id == scan.id)).scalar() or 0
    questions_count = s.execute(
        select(func.count(models.Question.id))
        .where(models.Question.scan_id == scan.id,
               models.Question.status == "open")).scalar() or 0
    domains_count = s.execute(
        select(func.count(models.BusinessDomain.id))
        .where(models.BusinessDomain.scan_id == scan.id)).scalar() or 0
    objects_count = s.execute(
        select(func.count(models.BusinessObject.id))
        .where(models.BusinessObject.scan_id == scan.id)).scalar() or 0
    flows_count = s.execute(
        select(func.count(models.BusinessFlow.id))
        .where(models.BusinessFlow.scan_id == scan.id)).scalar() or 0
    chain_count = s.execute(
        select(func.count(models.BusinessChain.id))
        .where(models.BusinessChain.scan_id == scan.id)).scalar() or 0
    confirmed_rules = s.execute(
        select(func.count(models.BusinessRule.id))
        .where(models.BusinessRule.scan_id == scan.id,
               models.BusinessRule.state.in_(
                   [models.RuleState.CONFIRMED, models.RuleState.MODIFIED]))
    ).scalar() or 0

    # 模块 Top（按行数）
    top_modules = [{"name": r.name, "lines": r.lines, "files": r.file_count}
                   for r in s.execute(
                       select(models.Module)
                       .where(models.Module.scan_id == scan.id)
                       .order_by(models.Module.lines.desc()).limit(10))
                   .scalars().all()]
    # 入口类型分布
    entry_dist = {}
    for kind, n in s.execute(
            select(models.CodeObject.entry_kind, func.count(models.CodeObject.id))
            .where(models.CodeObject.scan_id == scan.id,
                   models.CodeObject.is_entry.is_(True))
            .group_by(models.CodeObject.entry_kind)).all():
        entry_dist[kind or "other"] = n
    # 规则类型与可信度分布
    rule_types = {}
    for t, n in s.execute(
            select(models.BusinessRule.rule_type,
                   func.count(models.BusinessRule.id))
            .where(models.BusinessRule.scan_id == scan.id)
            .group_by(models.BusinessRule.rule_type)).all():
        rule_types[t.value if hasattr(t, "value") else str(t)] = n
    conf_buckets = {"high(>=75)": 0, "mid(50-74)": 0, "low(<50)": 0}
    for (c,) in s.execute(
            select(models.BusinessRule.confidence)
            .where(models.BusinessRule.scan_id == scan.id)).all():
        if c >= 75:
            conf_buckets["high(>=75)"] += 1
        elif c >= 50:
            conf_buckets["mid(50-74)"] += 1
        else:
            conf_buckets["low(<50)"] += 1
    return {
        "tier": scan.tier,
        "status": scan.status.value if hasattr(scan.status, "value") else str(scan.status),
        "scale": {k: m.get(k) for k in (
            "total_lines", "total_files", "excluded_files", "version_files",
            "version_lines", "encoding_samples", "lines_by_ext")},
        "stats": {**st, "rules": rules_count, "questions": questions_count,
                  "domains": domains_count, "objects": objects_count,
                  "flows": flows_count, "chain": chain_count,
                  "confirmed_rules": confirmed_rules},
        "detail": {
            "top_modules": top_modules,
            "entry_dist": entry_dist,
            "rule_types": rule_types,
            "confidence_buckets": conf_buckets,
            "scan_started": str(scan.started_at) if scan.started_at else None,
            "scan_finished": str(scan.finished_at) if scan.finished_at else None,
        },
        "scan_id": scan.id,
    }


@router.get("/projects/{pid}/modules")
def modules(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
        .order_by(models.Module.lines.desc())).scalars().all()
    return [{"id": r.id, "name": r.name, "lines": r.lines,
             "file_count": r.file_count, "is_archive": r.is_archive,
             "business_label": r.business_label} for r in rows]


@router.get("/projects/{pid}/objects")
def objects(pid: int, obj_type: models.ObjType | None = None, module: str | None = None,
            entry: bool | None = None, limit: int = Query(500, ge=1, le=5000),
            s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.CodeObject).where(models.CodeObject.scan_id == scan.id)
    if obj_type:
        q = q.where(models.CodeObject.obj_type == obj_type)
    if entry is not None:
        q = q.where(models.CodeObject.is_entry == entry)
    if module:
        q = q.join(models.Module).where(models.Module.name == module)
    q = q.order_by(models.CodeObject.start_line).limit(limit)
    rows = s.execute(q).scalars().all()
    return [{"id": r.id, "type": r.obj_type.value if hasattr(r.obj_type, "value") else str(r.obj_type),
             "name": r.name, "file": r.file_path,
             "start_line": r.start_line, "end_line": r.end_line,
             "annotations": r.annotations, "is_entry": r.is_entry,
             "entry_kind": r.entry_kind, "comment": r.comment} for r in rows]


@router.get("/projects/{pid}/calls")
def calls(pid: int, obj_id: int | None = None,
          limit: int = Query(1000, ge=1, le=10000),
          s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.CallEdge).where(models.CallEdge.scan_id == scan.id)
    if obj_id:
        q = q.where(models.CallEdge.from_obj_id == obj_id)
    q = q.limit(limit)
    rows = s.execute(q).scalars().all()
    return [{"id": r.id, "from_obj_id": r.from_obj_id, "to_obj_id": r.to_obj_id,
             "to_name": r.to_name,
             "resolve_type": r.resolve_type.value if hasattr(r.resolve_type, "value") else str(r.resolve_type),
             "file": r.file_path, "line": r.line} for r in rows]


@router.get("/projects/{pid}/apis")
def apis(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.ApiEndpoint).where(models.ApiEndpoint.scan_id == scan.id)
        .order_by(models.ApiEndpoint.path)).scalars().all()
    return [{"id": r.id, "method": r.method, "path": r.path,
             "controller_obj_id": r.controller_obj_id,
             "description": r.description} for r in rows]


@router.get("/projects/{pid}/tables")
def tables(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.DataTable).where(models.DataTable.scan_id == scan.id)
        .order_by(models.DataTable.name)).scalars().all()
    return [{"id": r.id, "name": r.name, "comment": r.comment,
             "source": r.source} for r in rows]


@router.get("/projects/{pid}/sql-ops")
def sql_ops(pid: int, table: str | None = None,
            limit: int = Query(1000, ge=1, le=5000),
            s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.SqlOperation).where(models.SqlOperation.scan_id == scan.id)
    if table:
        q = q.where(models.SqlOperation.table_name == table)
    q = q.limit(limit)
    rows = s.execute(q).scalars().all()
    return [{"id": r.id, "table": r.table_name, "op": r.op.value if hasattr(r.op, "value") else str(r.op),
             "fields": r.fields, "host_obj_id": r.host_obj_id,
             "sql": r.sql_text, "confidence": r.confidence,
             "file": r.file_path, "line": r.line} for r in rows]


@router.get("/projects/{pid}/biz-objects")
def biz_objects(pid: int, s: Session = Depends(get_session)):
    """业务对象清单（S2）。"""
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.BusinessObject).where(models.BusinessObject.scan_id == scan.id)
        .order_by(models.BusinessObject.id)).scalars().all()
    return [{"id": r.id, "name": r.name, "description": r.description,
             "source_tables": r.source_tables or [],
             "source_classes": r.source_classes or [],
             "actions": r.actions or [], "confidence": r.confidence,
             "confirmed": r.confirmed} for r in rows]


@router.get("/projects/{pid}/flows")
def biz_flows(pid: int, s: Session = Depends(get_session)):
    """业务流程清单（S4）。"""
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.BusinessFlow).where(models.BusinessFlow.scan_id == scan.id)
        .order_by(models.BusinessFlow.id)).scalars().all()
    return [{"id": r.id, "name": r.name, "description": r.description,
             "entry_name": r.entry_name,
             "business_steps": r.business_steps or [],
             "tech_steps": r.tech_steps or [], "confidence": r.confidence,
             "confirmed": r.confirmed} for r in rows]


@router.get("/projects/{pid}/chain")
def biz_chain(pid: int, s: Session = Depends(get_session)):
    """业务推演（S6）：驱动关系 + 业务主线 + 设计叙事。"""
    scan = _latest_scan(s, pid)
    chain = s.execute(
        select(models.BusinessChain)
        .where(models.BusinessChain.scan_id == scan.id)
        .order_by(models.BusinessChain.id.desc()).limit(1)).scalar_one_or_none()
    if chain is None:
        return {"has_chain": False, "message": "暂无业务推演（先运行标注生成业务对象与流程）"}
    raw_relationships = chain.relationships or []
    relationships = []
    assumptions = list(chain.assumptions or [])
    for relation in raw_relationships:
        if (
            isinstance(relation, dict)
            and relation.get("verification") in {"V1", "V2", "V3", "V4"}
        ):
            relationships.append(relation)
        elif isinstance(relation, dict):
            assumptions.append(
                f"历史推演关系「{relation.get('from', '?')} → "
                f"{relation.get('to', '?')}」缺少确定性证据，已从关系图移除"
            )
    assumptions = list(dict.fromkeys(str(item) for item in assumptions if item))
    confidence = chain.confidence
    if not relationships:
        confidence = min(confidence, 30)
    return {
        "has_chain": True, "id": chain.id,
        "mainline": chain.mainline or [],
        "relationships": relationships,
        "design_narrative": chain.design_narrative,
        "assumptions": assumptions,
        "confidence": confidence, "model": chain.model,
        "created_at": str(chain.created_at),
    }


@router.get("/documents/types")
def doc_types():
    from ..documents import DOC_TYPES
    return [{"key": k, "title": v[0], "desc": v[1]} for k, v in DOC_TYPES.items()]


@router.get("/projects/{pid}/documents/{doc_type}")
def generate_document(pid: int, doc_type: str, fmt: str = "md", draft: bool = False,
                      s: Session = Depends(get_session)):
    """生成文档（md/word）；draft=true 时才包含 AI 未确认业务知识。"""
    from ..documents import DOC_TYPES, _gather, render, to_word
    from fastapi.responses import Response
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"未知文档类型: {doc_type}，可选 {list(DOC_TYPES)}")
    scan = _latest_scan(s, pid)
    d = _gather(s, scan.id, include_unconfirmed=draft)
    md = render(doc_type, d)
    if fmt == "word":
        bio = to_word(md, DOC_TYPES[doc_type][0])
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition":
                     f'attachment; filename="{doc_type}.docx"'})
    return Response(content=md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{doc_type}.md"'})


class QaIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post("/projects/{pid}/qa")
def qa(pid: int, body: QaIn):
    """AI 问答：图谱检索 + 带证据回答（自动保存历史）。"""
    from ..db import SessionLocal
    from ..llm import LLMClient
    from ..semantics.qa import answer
    s = SessionLocal()
    try:
        scan = _latest_scan(s, pid)
        try:
            result = answer(s, scan.id, body.question, LLMClient())
            record = models.QaRecord(
                project_id=pid, question=body.question, answer=result)
            s.add(record)
            s.commit()
            return {"ok": True, "record_id": record.id, **result}
        except Exception as e:
            s.add(models.QaRecord(project_id=pid, question=body.question,
                                  error=str(e)[:500]))
            s.commit()
            return {"ok": False, "error": str(e)}
    finally:
        s.close()


@router.get("/projects/{pid}/qa-history")
def qa_history(pid: int, limit: int = Query(100, ge=1, le=500),
               s: Session = Depends(get_session)):
    """问答历史（持久化）。"""
    rows = s.execute(
        select(models.QaRecord).where(models.QaRecord.project_id == pid)
        .order_by(models.QaRecord.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "question": r.question, "answer": r.answer,
             "error": r.error,
             "created_at": str(r.created_at)} for r in rows]


@router.delete("/projects/{pid}/qa-history/{rid}")
def qa_history_delete(pid: int, rid: int, s: Session = Depends(get_session)):
    """删除单条问答历史。"""
    r = s.get(models.QaRecord, rid)
    if r is None or r.project_id != pid:
        raise HTTPException(404, "记录不存在")
    s.delete(r)
    s.commit()
    return {"ok": True}


class BatchConfirmIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["confirm", "discard"] = "confirm"
    user: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=4000)


@router.post("/projects/{pid}/rules/confirm-batch")
def confirm_rules_batch(
    pid: int, body: BatchConfirmIn, s: Session = Depends(get_session)
):
    """批量确认/废弃规则。"""
    scan = _latest_scan(s, pid)
    ids = list(dict.fromkeys(body.ids))
    if len(ids) != len(body.ids):
        raise HTTPException(400, "规则 ID 不允许重复")
    rules = s.execute(
        select(models.BusinessRule).where(
            models.BusinessRule.scan_id == scan.id,
            models.BusinessRule.id.in_(ids),
        )
    ).scalars().all()
    if len(rules) != len(ids):
        raise HTTPException(404, "部分规则不存在或不属于当前项目")
    for rule in rules:
        before = _rule_audit_snapshot(rule)
        if body.action == "confirm":
            if rule.state != models.RuleState.CONFIRMED:
                rule.confidence = min(100, rule.confidence + 10)
            rule.state = models.RuleState.CONFIRMED
        else:
            rule.state = models.RuleState.DISCARDED
        after = _rule_audit_snapshot(rule)
        s.add(models.Confirmation(
            scan_id=scan.id, user_id=body.user or "anonymous", target_type="rule",
            target_id=rule.id, action=body.action,
            before=before, after=after, note=body.note))
    s.commit()
    return {
        "ok": True,
        "affected": len(rules),
        "confirmed": len(rules) if body.action == "confirm" else 0,
        "discarded": len(rules) if body.action == "discard" else 0,
    }


@router.get("/projects/{pid}/rules/export")
def export_rules(
    pid: int, include_unconfirmed: bool = False, s: Session = Depends(get_session)
):
    """导出规则清单为 Excel；默认只包含已确认或人工修改的规则。"""
    from fastapi.responses import Response
    from openpyxl import Workbook
    scan = _latest_scan(s, pid)
    states = [models.RuleState.CONFIRMED, models.RuleState.MODIFIED]
    if include_unconfirmed:
        states.extend([models.RuleState.AI_LABELED, models.RuleState.CONFLICT])
    rows = s.execute(
        select(models.BusinessRule).where(
            models.BusinessRule.scan_id == scan.id,
            models.BusinessRule.state.in_(states),
        ).order_by(models.BusinessRule.confidence.desc())
    ).scalars().all()
    wb = Workbook()
    ws = wb.active
    ws.title = "业务规则清单"
    headers = ["规则名称", "类型", "模块", "触发方式", "前置条件", "处理逻辑",
               "状态变化", "数据变化", "后续动作", "异常处理", "影响范围",
               "证据文件", "证据行", "可信度", "确认状态"]
    ws.append(headers)
    for r in rows:
        values = [
            r.name, r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type),
            r.module, r.trigger, r.pre_condition, r.logic, r.status_change,
            r.data_change, r.follow_up, r.exception, r.impact,
            r.evidence_file, str(r.evidence_lines or []),
            r.confidence,
            r.state.value if hasattr(r.state, "value") else str(r.state),
        ]
        ws.append([_excel_safe(value) for value in values])
    for col, w in zip("ABCDEFGHIJKLMNO", [28, 12, 22, 20, 24, 46, 20, 24, 20,
                                          20, 20, 34, 12, 8, 12]):
        ws.column_dimensions[col].width = w
    bio = __import__("io").BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(
        content=bio.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="rules.xlsx"'})


@router.post("/projects/{pid}/modules/{mid}/analyze")
def analyze_module(pid: int, mid: int, force: bool = False):
    """模块深度分析：还原模块需求规格（功能/流程/数据/规则/缺口）。
    force=true 时绕过缓存完全重新分析。"""
    from ..db import SessionLocal
    from ..llm import LLMClient
    from ..semantics.module_analysis import analyze_module as do_analyze
    s = SessionLocal()
    try:
        scan = _latest_scan(s, pid)
        module = s.get(models.Module, mid)
        if module is None or module.scan_id != scan.id:
            raise HTTPException(404, "模块不存在")
        client = LLMClient(cache=not force)
        result = do_analyze(s, scan.id, mid, client, force=force)
        content = result["content"]
        return {
            "ok": True, "cached": result.get("cached", False),
            "module_name": content.get("module_name"),
            "purpose": content.get("purpose"),
            "features": content.get("features", []),
            "processes": content.get("processes", []),
            "data_model": content.get("data_model", []),
            "business_rules": content.get("business_rules", []),
            "external_dependencies": content.get("external_dependencies", []),
            "gaps": content.get("gaps", []),
            "markdown": result["markdown"],
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        s.close()


@router.get("/projects/{pid}/modules/{mid}/analysis")
def get_module_analysis(pid: int, mid: int, s: Session = Depends(get_session)):
    """获取已缓存的模块需求规格。"""
    from ..semantics.module_analysis import materialize_analysis
    scan = _latest_scan(s, pid)
    a = s.execute(
        select(models.ModuleAnalysis).where(
            models.ModuleAnalysis.scan_id == scan.id,
            models.ModuleAnalysis.module_id == mid)
    ).scalar_one_or_none()
    if a is None:
        return {"ok": False, "message": "尚未分析，先调用 POST analyze"}
    module = s.get(models.Module, mid)
    if module is None or module.scan_id != scan.id:
        raise HTTPException(404, "模块不存在")
    c, markdown = materialize_analysis(a, module.name)
    return {"ok": True, "module_name": c.get("module_name"),
            "purpose": c.get("purpose"), "features": c.get("features", []),
            "processes": c.get("processes", []),
            "data_model": c.get("data_model", []),
            "business_rules": c.get("business_rules", []),
            "external_dependencies": c.get("external_dependencies", []),
            "gaps": c.get("gaps", []), "markdown": markdown}


@router.post("/projects/{pid}/modules/analyze-all")
def analyze_all_modules(pid: int):
    """批量模块需求规格分析（异步，job 轮询 /label-jobs/{id}）。"""
    import threading
    from ..db import SessionLocal

    s = SessionLocal()
    try:
        scan = _latest_scan(s, pid)
        job = _create_project_job(s, pid, limit=0, stage="modules")
        job_id = job.id

        def _worker(job_id: int, scan_id: int):
            from concurrent.futures import ThreadPoolExecutor
            from ..llm import LLMClient
            from ..semantics.module_analysis import analyze_module as do_analyze
            ws = SessionLocal()
            try:
                scan = ws.get(models.Scan, scan_id)
                if scan is None or scan.status != models.ScanStatus.DONE:
                    raise RuntimeError("目标扫描已不可用")
                mods = ws.execute(
                    select(models.Module)
                    .where(models.Module.scan_id == scan.id)
                    .order_by(
                        models.Module.name.contains("businese").desc(),
                        models.Module.lines.desc())
                ).scalars().all()
                analyzed_ids = set(ws.execute(
                    select(models.ModuleAnalysis.module_id)
                    .where(models.ModuleAnalysis.scan_id == scan.id)
                ).scalars().all())
                pending = [m for m in mods if m.id not in analyzed_ids]
                total = len(mods)
                done = len(mods) - len(pending)
                initially_cached = done
                succeeded = done
                failed = 0
                errors: list[str] = []
                ws.close()

                ts = SessionLocal()
                job = ts.get(models.LabelJob, job_id)
                job.total = total
                job.progress = done
                ts.commit()
                ts.close()

                # 并发分析：每模块独立 session，避免事务串扰与唯一约束冲突
                client = LLMClient()
                lock = __import__("threading").Lock()

                def work(m):
                    nonlocal done, succeeded, failed
                    cs = SessionLocal()
                    error_message = None
                    try:
                        do_analyze(cs, scan.id, m.id, client)
                    except Exception as e:
                        error_message = f"模块 {m.name} 分析失败: {e}"
                    finally:
                        cs.close()
                    with lock:
                        done += 1
                        if error_message is None:
                            succeeded += 1
                        else:
                            failed += 1
                            if len(errors) < 20:
                                errors.append(error_message[:500])
                        cs2 = SessionLocal()
                        try:
                            j = cs2.get(models.LabelJob, job_id)
                            if j:
                                j.progress = done
                                j.stage = f"modules:{m.name}"
                                cs2.commit()
                        finally:
                            cs2.close()

                with ThreadPoolExecutor(max_workers=6) as ex:
                    list(ex.map(work, pending))

                fs = SessionLocal()
                try:
                    job = fs.get(models.LabelJob, job_id)
                    job.progress = done
                    job.result = {
                        "analyzed": succeeded,
                        "processed": done,
                        "failed": failed,
                        "total": total,
                        "cached": initially_cached,
                        "errors": errors,
                    }
                    if failed and succeeded == 0:
                        job.status = "failed"
                        job.error = f"所有 {failed} 个模块均分析失败"
                    else:
                        job.status = "done"
                    job.finished_at = models.utcnow()
                    fs.commit()
                finally:
                    fs.close()
            except Exception as e:
                fs = SessionLocal()
                try:
                    job = fs.get(models.LabelJob, job_id)
                    if job:
                        job.status = "failed"
                        job.error = str(e)[:2000]
                        job.finished_at = models.utcnow()
                        fs.commit()
                finally:
                    fs.close()

        threading.Thread(target=_worker, args=(job_id, scan.id), daemon=True).start()
        return {"ok": True, "job_id": job_id}
    finally:
        s.close()


@router.get("/projects/{pid}/modules/analyses")
def module_analyses_list(pid: int, s: Session = Depends(get_session)):
    """模块分析状态列表。"""
    scan = _latest_scan(s, pid)
    mods = s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
        .order_by(models.Module.name.contains("businese").desc(),
                  models.Module.lines.desc())).scalars().all()
    done_ids = set(s.execute(
        select(models.ModuleAnalysis.module_id)
        .where(models.ModuleAnalysis.scan_id == scan.id)).scalars().all())
    return [{"module_id": m.id, "name": m.name, "lines": m.lines,
             "file_count": m.file_count,
             "analyzed": m.id in done_ids} for m in mods]


@router.get("/projects/{pid}/modules/analyses/export")
def export_module_analyses(pid: int, s: Session = Depends(get_session)):
    """打包下载全部模块需求规格（zip）。"""
    from fastapi.responses import Response
    from ..semantics.module_analysis import materialize_analysis
    import io
    import zipfile
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.ModuleAnalysis)
        .where(models.ModuleAnalysis.scan_id == scan.id)
        .order_by(models.ModuleAnalysis.module_name)).scalars().all()
    if not rows:
        raise HTTPException(404, "还没有已分析的模块，先运行批量分析")
    module_names = dict(s.execute(
        select(models.Module.id, models.Module.name).where(
            models.Module.scan_id == scan.id
        )
    ).all())
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for a in rows:
            canonical_name = module_names.get(a.module_id, a.module_name)
            _content, markdown = materialize_analysis(a, canonical_name)
            name = canonical_name.replace("/", "__").replace("\\", "__")
            zf.writestr(f"{name}-需求规格.md", markdown)
        # 汇总索引
        index = ["# 模块需求规格索引\n"]
        for a in rows:
            index.append(f"- {module_names.get(a.module_id, a.module_name)}")
        zf.writestr("00-索引.md", "\n".join(index))
    bio.seek(0)
    return Response(content=bio.getvalue(),
                    media_type="application/zip",
                    headers={"Content-Disposition":
                             'attachment; filename="module-specs.zip"'})


@router.post("/projects/{pid}/questions/{qid}/resolve")
def resolve_question(pid: int, qid: int, s: Session = Depends(get_session)):
    """将待确认项标记为已解决。"""
    scan = _latest_scan(s, pid)
    q = s.get(models.Question, qid)
    if q is None or q.scan_id != scan.id:
        raise HTTPException(404, "待确认项不存在")
    q.status = "resolved"
    s.commit()
    return {"ok": True, "question_id": qid, "status": "resolved"}


@router.post("/projects/{pid}/chain/rerun")
def rerun_chain(pid: int, force: bool = True):
    """强制重新推演业务主线（绕过缓存重新调用 LLM）。"""
    from ..db import SessionLocal
    from ..llm import LLMClient
    from ..semantics.chain import run_chain as do_chain
    s = SessionLocal()
    try:
        scan = _latest_scan(s, pid)
        objs = s.execute(
            select(models.BusinessObject)
            .where(models.BusinessObject.scan_id == scan.id)).scalars().all()
        if len(objs) < 2:
            raise HTTPException(400, "业务对象不足 2 个，先运行标注生成业务对象")
        chain = do_chain(s, scan.id, LLMClient(cache=not force))
        if chain is None:
            s.rollback()
            raise HTTPException(400, "推演失败：对象不足")
        s.flush()
        s.query(models.BusinessChain).filter(
            models.BusinessChain.scan_id == scan.id,
            models.BusinessChain.id != chain.id,
        ).delete(synchronize_session=False)
        s.commit()
        return {"ok": True, "mainline": chain.mainline,
                "relationships": chain.relationships}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@router.get("/projects/{pid}/scan-tasks")
def scan_tasks(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid, completed_only=False)
    rows = s.execute(
        select(models.ScanTask).where(models.ScanTask.scan_id == scan.id)
        .order_by(models.ScanTask.id)).scalars().all()
    return [{"id": r.id, "shard_key": r.shard_key, "stage": r.stage,
             "status": r.status.value if hasattr(r.status, "value") else str(r.status),
             "retry_count": r.retry_count, "error": r.error,
             "file_count": len(r.file_list or [])} for r in rows]


@router.get("/projects/{pid}/domains")
def domains(pid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    rows = s.execute(
        select(models.BusinessDomain).where(models.BusinessDomain.scan_id == scan.id)
        .order_by(models.BusinessDomain.name)).scalars().all()
    return [{"id": r.id, "name": r.name, "description": r.description,
             "modules": r.modules or [], "confidence": r.confidence,
             "confirmed": r.confirmed} for r in rows]


@router.get("/projects/{pid}/rules")
def rules(pid: int, rule_type: models.RuleType | None = None,
          state: models.RuleState | None = None,
          module: str | None = None, domain_id: int | None = None,
          min_confidence: int | None = Query(None, ge=0, le=100),
          limit: int = Query(500, ge=1, le=5000),
          s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.BusinessRule).where(models.BusinessRule.scan_id == scan.id)
    if rule_type:
        q = q.where(models.BusinessRule.rule_type == rule_type)
    if state:
        q = q.where(models.BusinessRule.state == state)
    if module:
        q = q.where(models.BusinessRule.module == module)
    if domain_id:
        q = q.where(models.BusinessRule.domain_id == domain_id)
    if min_confidence is not None:
        q = q.where(models.BusinessRule.confidence >= min_confidence)
    q = q.order_by(models.BusinessRule.confidence.desc()).limit(limit)
    rows = s.execute(q).scalars().all()
    return [{"id": r.id, "name": r.name,
             "rule_type": r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type),
             "module": r.module, "domain_id": r.domain_id,
             "trigger": r.trigger, "pre_condition": r.pre_condition,
             "logic": r.logic, "status_change": r.status_change,
             "data_change": r.data_change, "follow_up": r.follow_up,
             "exception": r.exception, "impact": r.impact,
             "evidence_file": r.evidence_file,
             "evidence_lines": r.evidence_lines,
             "confidence": r.confidence,
             "state": r.state.value if hasattr(r.state, "value") else str(r.state),
             "model": r.model, "source_method_id": r.source_method_id} for r in rows]


@router.get("/projects/{pid}/rules/{rid}")
def rule_detail(pid: int, rid: int, s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    r = s.get(models.BusinessRule, rid)
    if r is None or r.scan_id != scan.id:
        raise HTTPException(404, "规则不存在")
    return {"id": r.id, "name": r.name,
            "rule_type": r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type),
            "module": r.module, "domain_id": r.domain_id,
            "trigger": r.trigger, "pre_condition": r.pre_condition,
            "logic": r.logic, "status_change": r.status_change,
            "data_change": r.data_change, "follow_up": r.follow_up,
            "exception": r.exception, "impact": r.impact,
            "evidence_file": r.evidence_file, "evidence_lines": r.evidence_lines,
            "evidence_snippet": r.evidence_snippet,
            "confidence": r.confidence,
            "state": r.state.value if hasattr(r.state, "value") else str(r.state),
            "model": r.model, "raw": r.raw,
            "source_method_id": r.source_method_id, "created_at": str(r.created_at)}


@router.get("/projects/{pid}/questions")
def questions(pid: int, status: str | None = None,
              s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.Question).where(models.Question.scan_id == scan.id)
    if status:
        q = q.where(models.Question.status == status)
    rows = s.execute(q.order_by(models.Question.id)).scalars().all()
    return [{"id": r.id, "qtype": r.qtype, "desc": r.desc,
             "suggestion": r.suggestion, "status": r.status,
             "rule_id": r.rule_id} for r in rows]


@router.get("/projects/{pid}/map")
def module_map(pid: int, limit: int = Query(40, ge=1, le=200),
               s: Session = Depends(get_session)):
    """模块级业务地图：Top 模块节点 + 模块间聚合调用边。"""
    scan = _latest_scan(s, pid)
    mods = s.execute(
        select(models.Module).where(models.Module.scan_id == scan.id)
        .order_by(models.Module.lines.desc()).limit(limit)).scalars().all()
    mod_ids = {m.id for m in mods}

    # 方法 → 模块（调用边两端都映射到模块）
    method_mod = dict(s.execute(
        select(models.CodeObject.id, models.CodeObject.module_id)
        .where(models.CodeObject.scan_id == scan.id)).all())

    edge_counts: dict[tuple[int, int], int] = {}
    for foid, toid in s.execute(
            select(models.CallEdge.from_obj_id, models.CallEdge.to_obj_id)
            .where(models.CallEdge.scan_id == scan.id,
                   models.CallEdge.from_obj_id.isnot(None),
                   models.CallEdge.to_obj_id.isnot(None))).all():
        f_mod, t_mod = method_mod.get(foid), method_mod.get(toid)
        if f_mod in mod_ids and t_mod in mod_ids and f_mod != t_mod:
            edge_counts[(f_mod, t_mod)] = edge_counts.get((f_mod, t_mod), 0) + 1

    top_edges = sorted(edge_counts.items(), key=lambda x: -x[1])[:200]
    return {
        "nodes": [{"id": m.id, "name": m.name, "lines": m.lines,
                   "file_count": m.file_count,
                   "category": m.name.split("/")[0]} for m in mods],
        "edges": [{"source": f, "target": t, "value": v}
                  for (f, t), v in top_edges],
    }


@router.get("/projects/{pid}/object-graph")
def object_graph(pid: int, module_id: int | None = None,
                 limit: int = Query(80, ge=1, le=500),
                 s: Session = Depends(get_session)):
    """模块内对象关系图：类节点 + 类间调用边（方法→文件→类近似）。"""
    scan = _latest_scan(s, pid)
    q = (select(models.CodeObject)
         .where(models.CodeObject.scan_id == scan.id,
                models.CodeObject.obj_type == models.ObjType.CLASS))
    if module_id:
        q = q.where(models.CodeObject.module_id == module_id)
    q = q.order_by(models.CodeObject.start_line).limit(limit)
    classes = s.execute(q).scalars().all()
    cls_ids = {c.id for c in classes}

    # 文件 → 类（每文件取第一个类，近似映射方法到类）
    file_class: dict[str, int] = {}
    for c in classes:
        file_class.setdefault(c.file_path, c.id)
    method_to_class: dict[int, int] = {}
    for oid, fpath in s.execute(
            select(models.CodeObject.id, models.CodeObject.file_path)
            .where(models.CodeObject.scan_id == scan.id,
                   models.CodeObject.obj_type == models.ObjType.METHOD)).all():
        cid = file_class.get(fpath)
        if cid is not None:
            method_to_class[oid] = cid

    counts: dict[tuple[int, int], int] = {}
    for foid, toid in s.execute(
            select(models.CallEdge.from_obj_id, models.CallEdge.to_obj_id)
            .where(models.CallEdge.scan_id == scan.id,
                   models.CallEdge.from_obj_id.isnot(None),
                   models.CallEdge.to_obj_id.isnot(None))).all():
        fc, tc = method_to_class.get(foid), method_to_class.get(toid)
        if fc in cls_ids and tc in cls_ids and fc != tc:
            counts[(fc, tc)] = counts.get((fc, tc), 0) + 1

    top_edges = sorted(counts.items(), key=lambda x: -x[1])[:150]
    return {
        "nodes": [{"id": c.id, "name": c.name, "file": c.file_path,
                   "start_line": c.start_line, "comment": c.comment,
                   "module_id": c.module_id}
                  for c in classes],
        "edges": [{"source": f, "target": t, "value": v}
                  for (f, t), v in top_edges],
    }


_EDITABLE_RULE_FIELDS = {
    "name", "rule_type", "module", "domain_id", "trigger", "pre_condition",
    "logic", "status_change", "data_change", "follow_up", "exception", "impact",
}
_TEXT_RULE_FIELDS = _EDITABLE_RULE_FIELDS - {"rule_type", "domain_id"}
_RULE_TEXT_LIMITS = {
    "name": 500,
    "module": 500,
    "trigger": 500,
    "pre_condition": 100_000,
    "logic": 100_000,
    "status_change": 100_000,
    "data_change": 100_000,
    "follow_up": 100_000,
    "exception": 100_000,
    "impact": 100_000,
}


def _rule_audit_snapshot(rule: models.BusinessRule) -> dict:
    snapshot = {field: getattr(rule, field) for field in _EDITABLE_RULE_FIELDS}
    snapshot["rule_type"] = (
        rule.rule_type.value if hasattr(rule.rule_type, "value") else str(rule.rule_type)
    )
    snapshot["state"] = (
        rule.state.value if hasattr(rule.state, "value") else str(rule.state)
    )
    snapshot["confidence"] = rule.confidence
    return snapshot


@router.post("/projects/{pid}/rules/{rid}/confirm")
def confirm_rule(pid: int, rid: int, body: ConfirmIn,
                 s: Session = Depends(get_session)):
    """人工确认/修改/废弃规则，写确认流水。"""
    scan = _latest_scan(s, pid)
    rule = s.get(models.BusinessRule, rid)
    if rule is None or rule.scan_id != scan.id:
        raise HTTPException(404, "规则不存在")
    before = _rule_audit_snapshot(rule)

    if body.action == "confirm":
        if rule.state != models.RuleState.CONFIRMED:
            rule.confidence = min(100, rule.confidence + 10)
        rule.state = models.RuleState.CONFIRMED
    elif body.action == "discard":
        rule.state = models.RuleState.DISCARDED
    elif body.action == "modify":
        if not body.fields:
            raise HTTPException(400, "modify 需要提供 fields")
        unknown = set(body.fields) - _EDITABLE_RULE_FIELDS
        if unknown:
            raise HTTPException(400, f"不允许修改字段: {', '.join(sorted(unknown))}")
        normalized = dict(body.fields)
        for key in _TEXT_RULE_FIELDS:
            if key in normalized and normalized[key] is not None and not isinstance(
                normalized[key], str
            ):
                raise HTTPException(400, f"字段 {key} 必须是文本或空值")
            if (
                key in normalized
                and isinstance(normalized[key], str)
                and len(normalized[key]) > _RULE_TEXT_LIMITS[key]
            ):
                raise HTTPException(
                    400,
                    f"字段 {key} 不能超过 {_RULE_TEXT_LIMITS[key]:,} 个字符",
                )
        if "rule_type" in normalized:
            try:
                normalized["rule_type"] = models.RuleType(normalized["rule_type"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "rule_type 值无效") from exc
        if "domain_id" in normalized and normalized["domain_id"] is not None:
            if not isinstance(normalized["domain_id"], int) or isinstance(
                normalized["domain_id"], bool
            ):
                raise HTTPException(400, "domain_id 必须是整数或空值")
            domain = s.get(models.BusinessDomain, normalized["domain_id"])
            if domain is None or domain.scan_id != scan.id:
                raise HTTPException(400, "domain_id 不属于当前项目")
        for key, value in normalized.items():
            setattr(rule, key, value)
        rule.state = models.RuleState.MODIFIED
    else:
        raise HTTPException(400, f"未知操作: {body.action}")

    after = _rule_audit_snapshot(rule)
    s.add(models.Confirmation(
        scan_id=scan.id, user_id=body.user or "anonymous", target_type="rule",
        target_id=rule.id, action=body.action,
        before=before, after=after, note=body.note))
    s.commit()
    return {"ok": True, "rule_id": rule.id,
            "state": rule.state.value if hasattr(rule.state, "value") else str(rule.state)}


@router.get("/projects/{pid}/confirmations")
def confirmations(pid: int,
                  target_type: Literal["rule", "domain", "flow"] | None = None,
                  limit: int = Query(200, ge=1, le=2000),
                  s: Session = Depends(get_session)):
    scan = _latest_scan(s, pid)
    q = select(models.Confirmation).where(models.Confirmation.scan_id == scan.id)
    if target_type:
        q = q.where(models.Confirmation.target_type == target_type)
    q = q.order_by(models.Confirmation.id.desc())
    rows = s.execute(q.limit(limit)).scalars().all()
    return [{"id": r.id, "user_id": r.user_id, "target_type": r.target_type,
             "target_id": r.target_id, "action": r.action,
             "before": r.before, "after": r.after, "note": r.note,
             "created_at": str(r.created_at)} for r in rows]


class LlmSettingsIn(BaseModel):
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(default=None, max_length=200)
    mode: Literal["api", "mock"] | None = None


class LabelIn(BaseModel):
    limit: int = Field(default=200, ge=0, le=5000)
    concurrency: int = Field(default=8, ge=1, le=32)
    entry_only: bool = False


@router.post("/projects/{pid}/label")
def run_label_api(pid: int, body: LabelIn):
    """触发 AI 语义标注（异步：立即返回 job_id，按项目轮询任务状态）。"""
    import threading
    from ..db import SessionLocal

    s = SessionLocal()
    try:
        project = s.get(models.Project, pid)
        if project is None:
            raise HTTPException(404, "项目不存在")
        job = _create_project_job(s, pid, limit=body.limit)
        job_id = job.id

        def _worker(job_id: int, limit: int, concurrency: int, entry_only: bool):
            from ..semantics.run import run_label
            ws = SessionLocal()
            try:
                job = ws.get(models.LabelJob, job_id)
                job.status = "running"

                def cb(done: int, total: int, stage: str):
                    # 独立短连接：回调在标注线程池线程中执行，
                    # 不能共享 run_label 的 session（跨线程会破坏事务）
                    cs = SessionLocal()
                    try:
                        j = cs.get(models.LabelJob, job_id)
                        if j:
                            j.progress = done
                            j.total = total
                            j.stage = stage
                            cs.commit()
                    finally:
                        cs.close()

                result = run_label(ws, pid, limit=limit,
                                   concurrency=concurrency,
                                   entry_only=entry_only,
                                   progress_cb=cb)
                job = ws.get(models.LabelJob, job_id)
                job.status = "done"
                job.result = result
                job.finished_at = models.utcnow()
                ws.commit()
            except Exception as e:
                ws.rollback()
                job = ws.get(models.LabelJob, job_id)
                if job:
                    job.status = "failed"
                    job.error = str(e)[:2000]
                    job.finished_at = models.utcnow()
                    ws.commit()
            finally:
                ws.close()

        threading.Thread(target=_worker, args=(job_id, body.limit,
                                               body.concurrency,
                                               body.entry_only),
                         daemon=True).start()
        return {"ok": True, "job_id": job_id}
    finally:
        s.close()


@router.get("/projects/{pid}/label-jobs/{job_id}")
def label_job(pid: int, job_id: int, s: Session = Depends(get_session)):
    """查询标注任务状态与进度。"""
    job = s.get(models.LabelJob, job_id)
    if job is None or job.project_id != pid:
        raise HTTPException(404, "任务不存在")
    return {
        "job_id": job.id, "project_id": job.project_id,
        "status": job.status, "stage": job.stage,
        "progress": job.progress, "total": job.total,
        "limit": job.limit, "result": job.result, "error": job.error,
        "created_at": str(job.created_at),
        "finished_at": str(job.finished_at) if job.finished_at else None,
    }


@router.get("/settings/llm")
def get_llm_settings():
    """返回 LLM 配置（key 脱敏）。"""
    from .. import settings_store
    st = settings_store.load_settings()
    key = st.get("llm_api_key", "")
    return {
        "base_url": st.get("llm_base_url"),
        "model": st.get("llm_model"),
        "mode": st.get("llm_mode"),
        "has_key": bool(key),
        "key_masked": (key[:6] + "***" + key[-4:]) if len(key) > 12 else "***",
    }


@router.post("/settings/llm")
def save_llm_settings(body: LlmSettingsIn):
    from .. import settings_store
    from ..security import UnsafeUrlError, validate_outbound_url
    overrides = {}
    if body.base_url is not None:
        try:
            overrides["llm_base_url"] = validate_outbound_url(
                body.base_url,
                purpose="llm",
                allow_private=_env_flag("C2B_ALLOW_PRIVATE_LLM"),
            )
        except UnsafeUrlError as exc:
            raise HTTPException(400, f"LLM Base URL 不安全: {exc}") from exc
    if body.api_key is not None:
        overrides["llm_api_key"] = body.api_key.strip()
    if body.model is not None:
        overrides["llm_model"] = body.model.strip()
    if body.mode is not None:
        overrides["llm_mode"] = body.mode
    st = settings_store.save_settings(overrides)
    return {"ok": True, "has_key": bool(st.get("llm_api_key"))}


@router.post("/settings/llm/test")
def test_llm_settings(body: LlmSettingsIn | None = None):
    """用当前（或传入）配置测试 LLM 连接。"""
    from ..llm import LLMClient, LLMError
    kwargs = {}
    if body:
        if body.base_url:
            kwargs["base_url"] = body.base_url.strip()
        if body.api_key:
            kwargs["api_key"] = body.api_key.strip()
        if body.model:
            kwargs["model"] = body.model.strip()
        if body.mode:
            kwargs["mode"] = body.mode
    try:
        client = LLMClient(**kwargs) if kwargs else LLMClient()
        if client.mode == "mock":
            return {"ok": True, "mode": "mock", "message": "当前为 mock 模式，无需 Key"}
        reply = client.chat_text("你是测试助手", "请只回复 OK", temperature=0)
        return {"ok": True, "mode": "api", "model": client.model,
                "message": f"连接成功：{reply[:50]}"}
    except LLMError as e:
        return {"ok": False, "mode": "api", "message": str(e)}
