"""核心图谱数据模型（对齐 mvp-tech-solution.md 第 4 章）。

MVP 建表：projects / scans / modules / code_objects / call_edges /
        api_endpoints / data_tables / data_fields / sql_operations / scan_tasks
业务层表（business_*）与 AI 语义层（M3）阶段新增，本次预留。
"""
import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, Enum as SAEnum, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Return naive UTC for existing timezone-naive database columns."""
    return datetime.now(UTC).replace(tzinfo=None)


class ScanMode(str, enum.Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ScanStatus(str, enum.Enum):
    PROBING = "probing"
    PARSING = "parsing"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ObjType(str, enum.Enum):
    CLASS = "class"
    METHOD = "method"
    FIELD = "field"
    INTERFACE = "interface"
    ENUM = "enum"


class ResolveType(str, enum.Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    UNKNOWN = "unknown"


class SqlOp(str, enum.Enum):
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    MERGE = "merge"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    tech_stack: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    scans: Mapped[list["Scan"]] = relationship(back_populates="project",
                                               cascade="all, delete-orphan")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    scan_mode: Mapped[ScanMode] = mapped_column(SAEnum(ScanMode), default=ScanMode.FULL)
    status: Mapped[ScanStatus] = mapped_column(SAEnum(ScanStatus), default=ScanStatus.PROBING)
    tier: Mapped[str | None] = mapped_column(String(8))          # S/M/L/XL
    meta: Mapped[dict | None] = mapped_column(JSON)               # 规模报告等
    stats: Mapped[dict | None] = mapped_column(JSON)              # 图谱统计
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="scans")


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("scan_id", "name", name="uq_module_scan_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))                # 如 businese/Sms01206
    lines: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    is_archive: Mapped[bool] = mapped_column(Boolean, default=False)
    business_label: Mapped[str | None] = mapped_column(String(500))  # AI 语义层填充


class CodeObject(Base):
    __tablename__ = "code_objects"
    __table_args__ = (
        Index("ix_codeobj_scan_name", "scan_id", "name"),
        Index("ix_codeobj_scan_file", "scan_id", "file_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("modules.id"))
    obj_type: Mapped[ObjType] = mapped_column(SAEnum(ObjType), index=True)
    name: Mapped[str] = mapped_column(String(500))
    fq_name: Mapped[str | None] = mapped_column(String(1000))      # 限定名（类=包.类，方法=类.方法）
    file_path: Mapped[str] = mapped_column(String(1024))
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text)
    annotations: Mapped[list | None] = mapped_column(JSON)         # 注解列表
    comment: Mapped[str | None] = mapped_column(Text)
    is_entry: Mapped[bool] = mapped_column(Boolean, default=False)  # main/API/定时/MQ 入口
    entry_kind: Mapped[str | None] = mapped_column(String(20))      # batch/api/schedule/mq/gui
    is_version_file: Mapped[bool] = mapped_column(Boolean, default=False)


class CallEdge(Base):
    __tablename__ = "call_edges"
    __table_args__ = (Index("ix_calledge_from", "scan_id", "from_obj_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    from_obj_id: Mapped[int] = mapped_column(ForeignKey("code_objects.id"), index=True)
    to_obj_id: Mapped[int | None] = mapped_column(ForeignKey("code_objects.id"))
    to_name: Mapped[str | None] = mapped_column(String(500))       # 解析不到时保留名字
    resolve_type: Mapped[ResolveType] = mapped_column(
        SAEnum(ResolveType), default=ResolveType.STATIC)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    line: Mapped[int | None] = mapped_column(Integer)


class ApiEndpoint(Base):
    __tablename__ = "api_endpoints"
    __table_args__ = (UniqueConstraint("scan_id", "method", "path",
                                       name="uq_api_scan_method_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    method: Mapped[str | None] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(1024))
    controller_obj_id: Mapped[int | None] = mapped_column(ForeignKey("code_objects.id"))
    description: Mapped[str | None] = mapped_column(Text)
    auth: Mapped[str | None] = mapped_column(String(500))


class DataTable(Base):
    __tablename__ = "data_tables"
    __table_args__ = (UniqueConstraint("scan_id", "name", name="uq_table_scan_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    comment: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="sql")  # ddl/sql/string


class DataField(Base):
    __tablename__ = "data_fields"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_field_table_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("data_tables.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    data_type: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False)


class SqlOperation(Base):
    __tablename__ = "sql_operations"
    __table_args__ = (Index("ix_sqlop_table", "scan_id", "table_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    table_id: Mapped[int | None] = mapped_column(ForeignKey("data_tables.id"))
    table_name: Mapped[str | None] = mapped_column(String(500))
    op: Mapped[SqlOp] = mapped_column(SAEnum(SqlOp), index=True)
    fields: Mapped[list | None] = mapped_column(JSON)              # 涉及的字段
    host_obj_id: Mapped[int | None] = mapped_column(ForeignKey("code_objects.id"))
    sql_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, default=100)  # 0-100
    file_path: Mapped[str | None] = mapped_column(String(1024))
    line: Mapped[int | None] = mapped_column(Integer)


class ScanTask(Base):
    """分片扫描任务（规模化执行引擎）。"""
    __tablename__ = "scan_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    shard_key: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(30), default="parse")
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.PENDING)
    file_list: Mapped[list | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class RuleState(str, enum.Enum):
    AI_LABELED = "ai_labeled"        # AI 识别
    CONFIRMED = "confirmed"          # 人工已确认
    MODIFIED = "modified"            # 人工已修改
    DISCARDED = "discarded"          # 人工已废弃
    CONFLICT = "conflict"            # 存在冲突


class RuleType(str, enum.Enum):
    VALIDATION = "validation"        # 校验
    CALCULATION = "calculation"      # 计算
    STATUS = "status"                # 状态
    PERMISSION = "permission"        # 权限
    TRIGGER = "trigger"              # 自动触发
    EXCEPTION = "exception"          # 异常
    OTHER = "other"


class BusinessDomain(Base):
    __tablename__ = "business_domains"
    __table_args__ = (UniqueConstraint("scan_id", "name", name="uq_domain_scan_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    modules: Mapped[list | None] = mapped_column(JSON)          # 归属模块名列表
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class BusinessRule(Base):
    __tablename__ = "business_rules"
    __table_args__ = (Index("ix_rule_scan_state", "scan_id", "state"),
                      Index("ix_rule_module", "scan_id", "module"))

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("business_domains.id"))
    source_method_id: Mapped[int | None] = mapped_column(ForeignKey("code_objects.id"))
    module: Mapped[str | None] = mapped_column(String(500))
    rule_type: Mapped[RuleType] = mapped_column(SAEnum(RuleType), default=RuleType.OTHER)
    name: Mapped[str | None] = mapped_column(String(500))       # 逻辑名称
    trigger: Mapped[str | None] = mapped_column(String(500))    # 触发方式
    pre_condition: Mapped[str | None] = mapped_column(Text)     # 前置条件
    logic: Mapped[str | None] = mapped_column(Text)             # 处理逻辑
    status_change: Mapped[str | None] = mapped_column(Text)     # 状态变化
    data_change: Mapped[str | None] = mapped_column(Text)       # 数据变化
    follow_up: Mapped[str | None] = mapped_column(Text)         # 后续动作
    exception: Mapped[str | None] = mapped_column(Text)         # 异常处理
    impact: Mapped[str | None] = mapped_column(Text)            # 影响范围
    evidence_file: Mapped[str | None] = mapped_column(String(1024))
    evidence_lines: Mapped[list | None] = mapped_column(JSON)   # [start, end]
    evidence_snippet: Mapped[str | None] = mapped_column(Text)  # 证据代码片段
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    state: Mapped[RuleState] = mapped_column(SAEnum(RuleState), default=RuleState.AI_LABELED)
    model: Mapped[str | None] = mapped_column(String(100))
    raw: Mapped[dict | None] = mapped_column(JSON)              # LLM 原始输出
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Question(Base):
    """待确认项。"""
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("business_rules.id"))
    qtype: Mapped[str | None] = mapped_column(String(30))       # 动态调用/外部不可见/命名推断等
    desc: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/resolved


class Confirmation(Base):
    """人工确认流水。"""
    __tablename__ = "confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Denormalized ownership boundary for fast, reliable project isolation.
    # Nullable keeps older audit rows readable after the lightweight migration.
    scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scans.id"), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(30))        # rule/domain/flow
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(20))             # confirm/modify/discard
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BusinessObject(Base):
    """业务对象（S2）：表+实体类+Service → 业务对象及其动作。"""
    __tablename__ = "business_objects"
    __table_args__ = (Index("ix_bizobj_scan", "scan_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("business_domains.id"))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    source_tables: Mapped[list | None] = mapped_column(JSON)
    source_classes: Mapped[list | None] = mapped_column(JSON)
    actions: Mapped[list | None] = mapped_column(JSON)          # 业务动作
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BusinessFlow(Base):
    """业务流程（S4）：入口 + 调用链 → 业务动作序列。"""
    __tablename__ = "business_flows"
    __table_args__ = (Index("ix_flow_scan", "scan_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    entry_method_id: Mapped[int | None] = mapped_column(ForeignKey("code_objects.id"))
    entry_name: Mapped[str | None] = mapped_column(String(500))
    business_steps: Mapped[list | None] = mapped_column(JSON)   # 业务层步骤
    tech_steps: Mapped[list | None] = mapped_column(JSON)       # 技术层步骤
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BusinessChain(Base):
    """业务推演（S6）：业务对象之间的驱动关系与业务主线（产品设计还原）。"""
    __tablename__ = "business_chains"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    mainline: Mapped[list | None] = mapped_column(JSON)         # 业务主线对象序列
    relationships: Mapped[list | None] = mapped_column(JSON)    # 对象间驱动关系
    design_narrative: Mapped[str | None] = mapped_column(Text)  # 产品设计逻辑叙事
    assumptions: Mapped[list | None] = mapped_column(JSON)      # 推断假设/待确认
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RuntimeEvidence(Base):
    """A test or observed-run fact attached to one semantic conclusion.

    ``target_type`` and ``target_id`` deliberately form a polymorphic link to
    a rule, flow, or chain.  Ownership is denormalized through ``scan_id`` so
    every API query can enforce the project boundary without trusting a
    client-supplied target id.  R0 is not stored: it is derived when a target
    has no runtime evidence.
    """
    __tablename__ = "runtime_evidence"
    __table_args__ = (
        Index(
            "ix_runtime_evidence_target",
            "scan_id", "target_type", "target_id", "level",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(20))  # rule/flow/chain
    target_id: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(2))          # R1/R2/R3; R0 is derived
    evidence_type: Mapped[str] = mapped_column(String(30))
    environment: Mapped[str] = mapped_column(String(30))   # test/controlled/staging/production
    summary: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(2048))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LabelJob(Base):
    """Web 后台任务（扫描、标注或模块规格）。

    The current MVP runs workers in-process.  ``heartbeat_at`` is therefore a
    lease, not a distributed lock: on a later startup a stale lease tells us
    the former process cannot still own the worker and the job can be made
    visibly retryable instead of remaining ``running`` forever.
    """
    __tablename__ = "label_jobs"
    __table_args__ = (
        Index(
            "uq_label_jobs_project_running",
            "project_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/done/failed
    limit: Mapped[int] = mapped_column(Integer, default=50)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(30), default="preparing")  # rules/objects/flows/chain
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ModuleAnalysis(Base):
    """模块深度分析：模块级需求规格（功能/流程/数据/规则/缺口）。"""
    __tablename__ = "module_analyses"
    __table_args__ = (UniqueConstraint("scan_id", "module_id",
                                       name="uq_analysis_scan_module"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id"), index=True)
    module_name: Mapped[str] = mapped_column(String(300))
    content: Mapped[dict | None] = mapped_column(JSON)          # 需求规格 JSON
    markdown: Mapped[str | None] = mapped_column(Text)          # 渲染后的需求规格
    model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QaRecord(Base):
    """AI 问答历史（持久化，刷新不丢失）。"""
    __tablename__ = "qa_records"
    __table_args__ = (Index("ix_qa_project", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict | None] = mapped_column(JSON)           # 11 段回答
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
