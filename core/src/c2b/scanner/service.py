"""扫描服务：创建项目 + 全流程扫描（CLI 与 Web 共用）。"""
from pathlib import Path

from sqlalchemy.orm import Session

from .graph import Scanner


def scan_project(s: Session, root: Path, name: str,
                 git_commit: str | None = None, module_depth: int = 2,
                 max_files: int | None = None, project_id: int | None = None,
                 progress=None) -> dict:
    """完整扫描：探测 → 分片 → 解析 → 落库。返回 stats。
    project_id 存在时复用已有项目（Web 导入场景），否则新建。"""
    sc = Scanner(s, root, name, git_commit=git_commit,
                 module_depth=module_depth, max_files=max_files)
    try:
        sc.create(project_id=project_id)
        if progress:
            progress("probe", 0, 0)
        report = sc.run_probe()
        java_total = sum(
            1
            for files in report.get("modules_detail", {}).values()
            for item in files
            if Path(item.get("path", "")).suffix.lower() == ".java"
        )
        if progress:
            progress("parse", 0, java_total)
        plan = sc.shard_plan(report)
        parse_progress = (
            (lambda n: progress("parse", n, java_total))
            if progress else None
        )
        sc.run_parse(report, shards=plan["shards"] if plan["enabled"] else None,
                     progress=parse_progress)
        sc.finish()
        return {"project_id": sc.project.id, "scan_id": sc.scan.id,
                "stats": sc.scan.stats, "tier": report["tier"]}
    except Exception as exc:
        if sc.scan is not None:
            sc.fail(str(exc))
        else:
            s.rollback()
        raise
