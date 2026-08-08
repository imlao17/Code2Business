"""SQLAlchemy engine / session 管理。开发默认 SQLite，部署切 PostgreSQL（C2B_DB_URL）。"""
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
        indexes = {
            index["name"]
            for index in inspector.get_indexes("label_jobs")
        }
        if "uq_label_jobs_project_running" not in indexes:
            columns = {
                column["name"]
                for column in inspector.get_columns("label_jobs")
            }
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
