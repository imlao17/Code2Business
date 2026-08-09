"""SQLAlchemy engine / session 管理。开发默认 SQLite，部署切 PostgreSQL（C2B_DB_URL）。"""
import os
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or config.DB_URL
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    created = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(created, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return created


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db():
    from . import models  # noqa: F401  确保模型注册
    Base.metadata.create_all(engine)
    _migrate_schema()
    recover_stale_label_jobs()


def job_lease_seconds() -> int:
    """Return the bounded in-process worker lease duration.

    A worker refreshes its lease independently of scan/LLM progress, so a
    five-minute default can safely identify jobs abandoned by an API restart
    while still allowing long clone or model calls to run normally.
    """
    try:
        value = int(os.environ.get("C2B_JOB_LEASE_SECONDS", "300"))
    except ValueError:
        value = 300
    return max(30, min(value, 24 * 60 * 60))


def recover_stale_label_jobs(session=None, *, lease_seconds: int | None = None,
                             force: bool = False) -> int:
    """Mark abandoned Web jobs failed so a project is never locked forever.

    The API currently owns workers with daemon threads.  Threads do not
    survive an API process restart, so a job with an expired heartbeat has no
    recoverable executor.  Preserve its partial result but mark the record
    failed with a retryable explanation.  Fresh leases are left alone unless
    the single-process Web API is known to have restarted and passes
    ``force=True`` from its own lifespan hook.
    """
    from . import models

    owns_session = session is None
    s = session or SessionLocal()
    try:
        now = models.utcnow()
        cutoff = now - timedelta(seconds=lease_seconds or job_lease_seconds())
        freshness = func.coalesce(
            models.LabelJob.heartbeat_at,
            models.LabelJob.started_at,
            models.LabelJob.created_at,
        )
        query = select(models.LabelJob).where(models.LabelJob.status == "running")
        if not force:
            query = query.where(freshness < cutoff)
        jobs = s.execute(query).scalars().all()
        message = (
            "任务租约已过期：API 进程可能重启或工作线程已中断；"
            "未完成的工作未被写入，请重新运行该任务。"
        )
        for job in jobs:
            job.status = "failed"
            job.error = message
            job.finished_at = now
            job.heartbeat_at = now
        if jobs:
            s.commit()
        return len(jobs)
    finally:
        if owns_session:
            s.close()


def _migrate_schema(target_engine=None) -> None:
    """Apply small additive migrations for existing MVP databases.

    The project did not previously ship a migration framework.  Keep this
    deliberately additive and idempotent so old SQLite/PostgreSQL installs can
    gain ownership metadata without losing audit rows.
    """
    migration_engine = target_engine if target_engine is not None else engine
    inspector = inspect(migration_engine)
    tables = set(inspector.get_table_names())
    if "confirmations" in tables:
        columns = {
            column["name"]
            for column in inspector.get_columns("confirmations")
        }
        if "scan_id" not in columns:
            with migration_engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE confirmations ADD COLUMN scan_id INTEGER"
                )
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_confirmations_scan_id "
                "ON confirmations (scan_id)"
            )
            if "business_rules" in tables:
                connection.exec_driver_sql(
                    "UPDATE confirmations SET scan_id = ("
                    "SELECT business_rules.scan_id FROM business_rules "
                    "WHERE business_rules.id = confirmations.target_id"
                    ") WHERE confirmations.scan_id IS NULL "
                    "AND confirmations.target_type = 'rule'"
                )

    if {"module_analyses", "modules"}.issubset(tables):
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE module_analyses SET module_name = ("
                "SELECT modules.name FROM modules "
                "WHERE modules.id = module_analyses.module_id"
                ") WHERE EXISTS (SELECT 1 FROM modules "
                "WHERE modules.id = module_analyses.module_id) "
                "AND module_analyses.module_name <> ("
                "SELECT modules.name FROM modules "
                "WHERE modules.id = module_analyses.module_id)"
            )

    if "label_jobs" in tables:
        columns = {
            column["name"] for column in inspector.get_columns("label_jobs")
        }
        with migration_engine.begin() as connection:
            if "started_at" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE label_jobs ADD COLUMN started_at DATETIME"
                )
            if "heartbeat_at" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE label_jobs ADD COLUMN heartbeat_at DATETIME"
                )
            if "created_at" in columns:
                connection.exec_driver_sql(
                    "UPDATE label_jobs SET started_at = COALESCE(started_at, created_at) "
                    "WHERE started_at IS NULL"
                )
                connection.exec_driver_sql(
                    "UPDATE label_jobs SET heartbeat_at = "
                    "COALESCE(heartbeat_at, started_at, created_at) "
                    "WHERE heartbeat_at IS NULL"
                )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_label_jobs_heartbeat_at "
                "ON label_jobs (heartbeat_at)"
            )
        indexes = {
            index["name"]
            for index in inspector.get_indexes("label_jobs")
        }
        if "uq_label_jobs_project_running" not in indexes:
            assignments = ["status = 'failed'"]
            if "error" in columns:
                assignments.append(
                    "error = COALESCE(NULLIF(error, ''), "
                    "'并发任务迁移时被较新的运行任务取代')"
                )
            if "finished_at" in columns:
                assignments.append(
                    "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)"
                )
            with migration_engine.begin() as connection:
                # 老版本可能已通过竞态写入多个 running。保留每个项目最新
                # 的一条，再创建部分唯一索引；全程只做可回退的状态修复。
                connection.exec_driver_sql(
                    "UPDATE label_jobs SET " + ", ".join(assignments) +
                    " WHERE status = 'running' AND id NOT IN ("
                    "SELECT keep_id FROM ("
                    "SELECT MAX(id) AS keep_id FROM label_jobs "
                    "WHERE status = 'running' GROUP BY project_id"
                    ") AS active_jobs)"
                )
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_label_jobs_project_running "
                    "ON label_jobs (project_id) WHERE status = 'running'"
                )


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
