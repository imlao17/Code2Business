# Code2Business MVP 技术方案

> 依据：code-to-business-prd.md V1.0
> 技术决策：Python 全栈 + 可选云 LLM Provider / Agent Native
> 文档版本：V1.1
> 日期：2026-08-05

> **当前 MVP 实现边界（2026-08-08）**：本文同时保留了目标架构。当前代码仅实现
> Java tree-sitter 扫描，任务执行使用进程内后台线程而非 Celery/Redis，数据库升级
> 使用幂等的轻量增量迁移而非 Alembic，问答使用有上限的图谱关键词检索而非
> embedding。调用目标无法可靠唯一解析时一律标记 `UNKNOWN`，不做臆测型静态边。
> 正式文档只纳入人工确认的业务域、对象、流程和已确认/已修改规则；`--draft` 才包含 AI 未确认内容与 AI 主线。
> 云端语义已支持 OpenAI Compatible 与 Anthropic Native；Agent Skills 默认使用当前 Agent 的模型，
> 经由确定性证据导出和结构化校验导入业务语义，不需要另配外部 Key。Web 后台没有当前 Agent 会话，
> 因此仅支持云端 Provider。

---

## 1. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│  本地扫描器（Python CLI，部署在客户内网）                      │
│  Git拉取 → 分片 → 静态解析 → 调用图 → SQL/DDL → 图谱落库     │
└────────────────────────────┬────────────────────────────────┘
                             │ 结构化图谱 JSON（可脱敏）
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  Web 服务端（FastAPI，可私有化部署）                           │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ 图谱存储    │  │ AI 语义层     │  │ 文档生成/问答/确认    │ │
│  │ PostgreSQL │  │ LLM / Agent   │  │ 工作流               │ │
│  │            │  │ 语义标注管线  │  │                      │ │
│  └────────────┘  └──────────────┘  └──────────────────────┘ │
└────────────────────────────┬────────────────────────────────┘
                             │ REST API
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  Web 工作台（Vue 3 + Element Plus + ECharts + AntV X6）      │
│  项目总览 / 业务地图 / 流程与规则 / 数据与接口 / 文档与验收    │
└─────────────────────────────────────────────────────────────┘
```

**核心原则（继承 PRD 12.1）**：不采用"整库喂给大模型自由总结"的方式。

- 确定性分析（代码解析、调用链、SQL、DDL）全部用静态分析器完成，不用 LLM；
- 云端 LLM 或当前 Agent 只负责“语义标注”（命名、归纳、描述、转译），且必须基于确定性分析产出的结构化事实；
- 所有 AI / Agent 结论必须挂证据、标可信度、走人工确认；Agent Native 导入会验证规则的文件、方法和行号均属于本次完成扫描。

---

## 2. 技术选型

| 组件 | 选型 | 说明 |
|---|---|---|
| 代码解析 | tree-sitter（python 绑定） | Java/C++ 实现，解析快（10-50 万行/秒），支持增量解析（规模化需要）；javalang 作为回退方案 |
| SQL 解析 | SQLGlot | 支持 MySQL/PostgreSQL 方言，可提取表/字段读写 |
| 配置解析 | PyYAML / python-dotenv | application.yml、bootstrap.yml |
| 图谱存储 | PostgreSQL 16 + JSONB | 关系模型表达"对象-关系-证据"，避免 Neo4j 运维成本；图上分析（PageRank、连通域）用 networkx 在扫描端离线计算，结果落 PG |
| 任务队列 | Celery + Redis | AI 标注管线、大项目分片扫描的异步调度、断点续扫 |
| Web 后端 | FastAPI + SQLAlchemy 2.0 + Alembic | 异步 API，pydantic 校验 |
| 前端 | Vue 3 + Vite + Element Plus | 页面结构对齐 PRD 15 |
| 图可视化 | ECharts（关系图/桑基图）+ AntV X6（流程图） | X6 支持编辑，用于人工调整流程 |
| 云端 LLM 接入 | 裸调 HTTP：OpenAI Compatible 或 Anthropic Messages API | 不引 LangChain；统一 `LLMClient` 适配层，兼容服务可配地址，Anthropic 使用原生协议 |
| Agent Native | `export-evidence` → 当前 Agent 分析源码 → `import-semantic` | 仅 CLI / Skills；校验后原子入库，不伪装为服务端 Provider |
| Embedding | OpenAI 兼容 embedding API 或本地 bge-m3 | 问答检索用，MVP 先走图谱检索为主、向量为辅 |
| 文档生成 | Jinja2（Markdown/HTML）+ python-docx（Word）+ 简单 HTML→PDF | 优先 Markdown/Word，PDF 后置 |
| 测试样本 | 开发期：若依 RuoYi-Vue（开源，约 15-20 万行，语义层主样本）；验收期：**真实遗留系统 n-s-modernization-origin**（富士通 APWORKS/JBK 老式批处理+GUI 系统，247 万行 Java，角色 = 规模化验证样本） | 样本已锁定 |

---

## 3. 扫描器设计（本地 CLI）

### 3.1 扫描管线（Pipeline）

每个阶段独立输出中间产物（JSON/parquet），支持**断点续扫**与**增量扫描**：

```text
stage 1 fetch     Git clone / checkout 指定版本
stage 2 discover  文件清单、按顶层包名分片（规模化并行单位）
stage 3 parse     tree-sitter 提取符号：类/方法/字段/注解/注释/行号
stage 4 sql       提取 SQL：MyBatis XML、注解 SQL、字符串模板 SQL → SQLGlot 解析
stage 5 ddl       解析建表语句 → 表/字段/索引/外键/枚举注释
stage 6 frontend  Vue 路由、菜单、按钮 → Axios 请求（路径+方法）
stage 7 config    解析 application.yml、权限注解、定时任务、MQ 监听
stage 8 graph     汇聚为统一代码图谱 → 落库
```

### 3.2 关键分析细节（确定性层，全部不依赖 LLM）

| 分析项 | 实现要点 |
|---|---|
| 方法调用图 | 按类内解析方法调用表达式 → 解析目标类型 → 跨类/跨包解析；解析不到（反射/动态）记为"中断节点" |
| MyBatis 绑定 | Mapper 接口 ↔ XML `namespace` 匹配；SQL `id` ↔ 接口方法；XML 中 `${}` 动态 SQL 标记为低可信 |
| Controller→Service→Mapper 链路 | 按依赖注入类型解析（构造器/字段/Spring 注解），追踪完整调用链 |
| SQL 读写 | SQLGlot 输出 `表 × {insert/update/delete/select} × 字段`，关联到宿主方法 |
| HTTP 调用 | RestTemplate / Feign / 前端 Axios 的 URL 提取；Feign 接口注解直接映射 |
| 状态枚举 | 枚举类 + 字段名 hint（`status/state/type/flag`）启发式识别，结合 `@EnumValue` 等注解 |
| 定时任务 | `@Scheduled`、Quartz 配置、`xxl-job` 注解 |
| 消息消费者 | `@RabbitListener`、`@KafkaListener` 提取 topic/queue |
| 规则候选 | if 分支（含比较/数值运算）、`throw new XxxException("业务文案")`、校验方法、金额/数量字段运算、状态字段赋值、insert/update 语句 |
| 前端↔后端映射 | Axios 路径与方法名 → 匹配 Controller `@RequestMapping` 组合路径 |
| 数据血缘（MVP） | 方法级血缘：页面字段→接口返回字段→Service 方法→SQL select 字段→表字段；不做表达式级精确数据流（明确为 MVP 边界） |

### 3.3 证据模型

每个业务结论（规则/流程/对象/口径）关联：

```text
结论 ──┬─ 代码证据：文件 + 方法 + 起止行号（git blob 定位，保证可跳转）
      ├─ 数据证据：表 + 字段 + SQL 语句
      ├─ 结构证据：调用链路径（可展开）
      └─ 人工证据：确认人 + 时间 + 修改记录（版本化）
```

---

## 4. 图谱数据模型（PostgreSQL 核心表）

对齐 PRD 9.1 的 15 个实体，MVP 落表如下：

| 表 | 说明 | 关键字段 |
|---|---|---|
| `projects` | 分析项目 | name, tech_stack, repo_url, status |
| `scans` | 扫描版本（支持版本对比/增量） | project_id, git_commit, scan_mode, status, stats JSONB |
| `modules` | 代码模块/顶层包 | project_id, name, parent_id |
| `code_objects` | 类/方法 | type(class/method/field), name, file_path, start_line, end_line, signature, annotations, comment |
| `call_edges` | 方法调用边 | from_obj, to_obj, resolve_type(static/dynamic/unknown), file, line |
| `api_endpoints` | HTTP 接口 | method, path, controller_obj, auth, swagger_desc, in_use |
| `data_tables` | 表 | name, comment, source(DDL/XML/string), table_type |
| `data_fields` | 字段 | table_id, name, type, comment, is_pk, is_status_hint |
| `sql_operations` | SQL 读写 | table_id, op(read/write), fields JSONB, host_obj, sql_text, confidence |
| `business_domains` | 业务域 | name, module_ids, ai_labeled, confirmed |
| `business_objects` | 业务对象 | name, type, source_objs, data_table_id, status |
| `business_rules` | 规则卡片 | type(校验/计算/状态/权限/触发/异常), name, desc, trigger, pre_condition, logic, status_change, data_change, follow_up, exception, impact, confidence, state(AI识别/待确认/已确认/已修改/已废弃/冲突) |
| `rule_evidence` | 规则-证据关联 | rule_id, evidence_type, ref(文件/行/表/字段/调用链), confidence_factor |
| `business_flows` / `flow_nodes` | 流程及节点 | entry_type(页面/API/MQ/定时), layer(business/tech), next_id, branch |
| `questions` | 待确认项 | type, desc, suggestion, status |
| `confirmations` | 人工确认流水 | user_id, target_type, target_id, action, before/after JSONB, ts |
| `documents` | 生成文档 | type, template, content JSONB, export_path, scan_version |
| `scan_tasks` | 分片扫描任务（规模化） | project_id, shard_key, stage, status, worker, retry |

> MVP 不引入图数据库。调用图/血缘图以 `call_edges`、`sql_operations` 两张边表承载，前端按需加载局部子图。

---

## 5. AI 语义层设计

### 5.1 云端 LLM Provider 适配层

```text
LLMClient
  ├─ OpenAICompatibleClient（OpenAI/DeepSeek/Kimi/Ollama/自建兼容端，base_url 可配）
  └─ AnthropicMessagesClient（Claude 原生 `/v1/messages`，base_url 通常为 api.anthropic.com）
```

所有调用走统一的：结构化 JSON 输出（本地 schema 校验）、重试、限流、成本计数、结果缓存（相同输入不重复调用）。自定义地址必须由使用者明确选择协议，不做“任意 URL 自动猜测”。

### 5.2 Agent Native 语义桥接（CLI / Skills）

交互式 Agent 的会话模型不能被独立 CLI 或长期运行的 Web 服务安全继承，因此不把它包装成 HTTP API。Skills 的默认路径为：

```text
完成扫描 → export-evidence（模块/方法/API/表/行号）
         → 当前 Agent 阅读源码与证据，生成 contract_version=1.0 的 JSON
         → import-semantic（验证规则证据文件+行号+方法）
         → business_* / questions（原子落库）→ 人工确认 / 文档
```

导入失败时整个事务回滚；仅替换未确认的机器生成语义，已确认或已修改内容保留。此路径不需要 C2B 配置 API Key，但模块规格与问答仍使用云端 Provider。

### 5.3 云端标注管线（后台任务，按阶段串行推进）

```text
S1 业务域识别    输入：顶层包名+模块+Controller/表注释 → 输出：业务域树（粒度粗、token 少、全量）
S2 业务对象识别  输入：数据表+实体类+Service → 输出：业务对象及其来源映射
S3 规则提取      输入：规则候选（确定性层产出）+ 相关代码片段 + 表结构 → 输出：规则卡片
S4 流程归纳      输入：API 链路（技术层）+ 规则 → 输出：业务动作序列（业务层）
S5 状态机还原    输入：状态字段+赋值点+枚举 → 输出：状态流转图
```

每阶段输出进入 `business_*` 表，状态为「AI 识别」，人工确认后升级为「已确认」并锁定基线。

### 5.4 语义约束（全路径统一）

- 只允许基于给定的代码事实回答，禁止臆测；
- 云端输出或 Agent 导入都遵循固定 JSON Schema / contract；
- 每条结论必须回填证据引用（文件/行号/表名/SQL id）；
- 不确定的内容进 `questions` 表，不混入结论。

### 5.5 可信度计算（继承 PRD 11）

确定性层产出证据完整度 + 云端 LLM 自评一致度（若有）→ 规则引擎打分：

```text
可信度 = 基础分(50)
       + 调用链完整(+10) + 数据流证据(+10) + 运行时/测试证据(+10)
       + 多处实现一致(+10) + 人工已确认(+10)
       - 反射/动态SQL(-10) - 外部系统不可见(-5) - 调用链中断(-10)
       - 逻辑冲突(-10) - 仅命名推断(-15)
等级：高≥90 / 较高75-89 / 中50-74 / 低<50
```

### 5.6 问答（RAG + 图谱检索）

1. 问题改写（LLM，识别业务实体/意图）；
2. 图谱检索：命中业务对象/规则/流程/接口/表 → 取相关代码证据片段 + 血缘路径；
3. 向量检索兜底：代码片段 embedding 检索（MVP 阶段图谱优先）；
4. LLM 组装回答，强制输出 11 段结构（结论/触发/步骤/状态/数据/后续/异常/影响/证据/可信度/待确认），未覆盖调用必须显式标注；
5. 每个证据可点击跳转代码与业务卡片。

---

## 6. Web API（FastAPI 主要端点）

```text
POST   /api/projects                         创建项目
POST   /api/projects/{id}/scans              触发扫描（本地 CLI 或服务器端）
GET    /api/projects/{id}/overview           系统全景统计
GET    /api/projects/{id}/domains            业务域树
GET    /api/projects/{id}/map                业务地图（子图数据）
GET    /api/projects/{id}/rules?type=&state= 规则清单
GET    /api/projects/{id}/rules/{rule_id}    规则卡片详情（含证据）
POST   /api/projects/{id}/rules/{rule_id}/confirm  人工确认/修改（版本化）
GET    /api/projects/{id}/flows              业务流程
POST   /api/flows/{id}/nodes                 人工调整流程节点
GET    /api/projects/{id}/lineage?field=     数据血缘
GET    /api/projects/{id}/apis               接口清单
POST   /api/projects/{id}/qa                 问答（SSE 流式）
GET    /api/projects/{id}/questions          待确认项
POST   /api/projects/{id}/documents          生成文档（指定模板）
GET    /api/projects/{id}/versions/compare   版本对比
GET    /api/projects/{id}/scan-tasks         分片任务状态（规模化）
```

---

## 7. Web 工作台页面（对齐 PRD 15）

| 菜单 | 页面 | 核心组件 |
|---|---|---|
| 项目总览 | 统计卡片 + 最近扫描 + 待确认/高风险清单 | ECharts 统计图 |
| 业务地图 | 左：业务域树 / 中：对象关系图 / 右：详情+证据+可信度 | ECharts 关系图 |
| 流程与规则 | 业务流程视图（业务层/技术层切换）、规则清单与卡片、人工确认 | AntV X6 |
| 数据与接口 | ER 图、字段血缘、数据口径、API 清单 | ECharts 桑基图 |
| 文档与验收 | 模板选择、预览、导出、测试用例、验收清单 | 文档渲染器 |

人工确认贯穿全流程：规则确认、域合并/拆分、流程节点调整、误判标记，全部写 `confirmations` 流水。

---

## 8. 文档生成

- 模板引擎：Jinja2（数据来自已确认知识，**禁止无证据结论进入正式文档**，未确认内容单独标注）；
- MVP 模板优先级：系统现状说明书、功能清单、业务规则清单、数据字典、接口清单、验收清单、重构需求规格（对齐 PRD 10.11）；
- 输出格式：Markdown（默认）、Word（python-docx）；PDF/HTML/BPMN 后置。

---

## 9. 规模化执行引擎（探测 → 自适应分片 → 分层推进）

**设计目标：扫描器先探测项目规模，自动生成分片/分区方案并调度执行，目标能力覆盖 200 万行以上。**

核心原则：

1. **规模先探测，策略后决定**：任何项目进入管线后，第一步是秒级规模探测，输出规模报告，据此自动选择档位策略，无需人工配置；
2. **分片是默认路径**：≥100 万行一律分片并行；<100 万行单机全量（分片引擎可关闭）；
3. **确定性分析全量、语义标注分层**：静态分析（解析/调用图/SQL/DDL）对所有代码全量执行；云端 LLM 标注按价值分层推进，进度与覆盖对用户可见。Agent Native 可按相同证据契约分批导入，但不产生云端 token 成本。

### 9.1 分水岭与档位判定

| 档位 | 规模 | 扫描模式 | 分片数 | 语义标注策略 | 首次全流程时长 |
|---|---|---|---|---|---|
| S | ≤10 万行 | 单机全量 | 0 | 全量深标 | 0.5-1 小时 |
| M | 10-100 万行 | 单机全量 | 0 | 核心层全量 + 非核心规则化 | 2-4 小时 |
| L | 100-200 万行 | 分片并行 | 4-8 片 | 优先级队列（L2 覆盖 80%） | 6-10 小时 |
| XL | 200 万+ | 分片并行 + 分层 | 8-16 片 | 优先级 + token 预算 + 分批 | 核心 8 小时内出，全量分批 |

核心瓶颈不是解析（tree-sitter 快），而是 **云端 LLM 标注的调用量/时长** 与 **Python 侧图谱构建的内存**。大规模关键手段：探测、分片、分层、优先级、预算、增量；Agent Native 的瓶颈转为 Agent 可用上下文和人工审阅节奏。

### 9.2 阶段 0：代码量探测（秒级）

扫描管线第一个 stage，不解析 AST，只做文件级统计与目录归属推导：

```json
{
  "total_lines": 2100000,
  "total_files": 8200,
  "lines_by_language": { "java": 1600000, "vue": 300000, "xml": 120000, "sql": 50000, "yaml": 8000 },
  "lines_by_top_package": [
    { "pkg": "com.acme.erp", "lines": 680000, "files": 2100 },
    { "pkg": "com.acme.base", "lines": 420000, "files": 1500 }
  ],
  "giant_files": [ { "file": "src/main/java/com/acme/erp/generated/Model.java", "lines": 15000 } ],
  "frontend_lines": 380000,
  "tier": "XL",
  "worker_recommend": 12,
  "shard_plan": { "shards": 14, "avg_lines_per_shard": 140000 }
}
```

- 顶层包归属：按目录前缀推导（`com/acme/erp/...`），不需要解析语法树；
- **真实仓库预处理**（实战教训，来自 n-s-modernization-origin 样本）：
  - 排除非源码产物：`.class`、`.jar`、`.metadata/`（Eclipse 工作区）、`.project/.classpath`、`out/`、日志文件；
  - **历史版本目录识别**：按日期/版本命名的归档目录（如 `common/sms_menusystem/20090909_新規作成`、`20160302_xxx`）识别为"归档代码"，与当前活跃代码分开统计，默认只分析最新版本链；
  - **编码检测**：逐文件检测（UTF-8 / Shift-JIS / GBK），Shift-JIS 等非 UTF-8 文件标注并转码后再解析，注释乱码不阻塞分析；
  - 无 git 历史或目录型仓库：跳过 git 增量能力，按目录快照扫描；
- 探测结果写入 `scans.meta`，用户可在 Web 工作台查看「规模报告」；
- 档位判定规则固化：`tier = S(≤10万) / M(≤100万) / L(≤200万) / XL(>200万)`，**判定结果决定后续所有执行参数**。

### 9.3 阶段 1：全局符号索引（L0 结构快扫，分钟级）

分片并行之前，必须先有全局符号表，否则跨片类型解析无法进行：

- 全量扫描所有文件，只提取**符号表**：类名/方法签名/注解取值/API 路径/表名（不构建完整 AST 与调用图），分钟级完成；
- 符号表落 PG `code_objects`（`scan_version` 标记），供所有分片 worker 做跨片类型解析；
- L0 完成后系统全景（应用/模块/API/表统计）即可展示——**客户在扫描启动后分钟级看到全景**。

### 9.4 分片算法（贪心装载 + 超载包递归切分）

**输入**：阶段 0 的文件清单 + 行数 + 顶层包归属。
**目标**：N 片，每片行数落在 `目标片行数 × 0.7 ~ 1.3` 区间。

```text
N = clamp(总行数 / 8万, worker数, 24)        # 100万行→约12片；200万行→约25片（受worker上限截断）
worker数 = min(CPU核数, 16)                    # 单片内存预算 ≤ 4GB（AST+符号）

算法（贪心装载）：
1. 按顶层包分组，组内文件按行数降序排列
2. 依序装载到当前片，超出容量则开新片
3. 超载包（包行数 > 单片容量×1.2）：按二级包/子目录递归切分
   最小切分单位是文件；巨型单文件（>2万行，多为生成代码）独立成片并标记
4. 均衡调整：用小型包填充缺口片，保证各片行数均衡
```

分片结果写入 `scan_tasks`（shard_key = 包前缀列表或文件清单），是**可重入**的：任何一片失败，只重扫该片。

### 9.5 并行解析与跨片合并

```text
阶段2 分片解析（并行）  每片独立 worker：AST → 符号/调用边/SQL/DDL/规则候选 → 本地中间产物
                       跨片类型引用 → 查 PG 全局符号索引；解析不到 → 标记「中断节点」
阶段3 合并落库         全部片完成后：调用边 → call_edges；SQL → sql_operations；产物校验与统计
阶段4 前端/配置/DDL     独立并行任务（Vue 路由、YAML、建表脚本，不与 Java 分片争抢 worker）
```

- 中间产物按片落盘（JSON/parquet），合并阶段可反复重放；
- 合并后做一致性校验：符号总数 = Σ 各片符号数，防止丢片。

### 9.6 分层推进与优先级（语义层，全部异步可中断）

```text
L0 结构快扫    阶段1已完成 → 出「系统全景」
L1 骨架语义    业务域、业务对象、模块归属（聚合粒度，token 少，全量）
L2 核心深标    按优先级队列标注规则与流程：
               优先级 = 调用热度(被调用次数) + 数据表重要性(被SQL引用次数) + 是否涉金额/状态
               → 前 20% 高价值方法覆盖 80% 业务，先交付核心成果
L3 边缘按需    长尾逻辑（低热度工具方法、罕见分支）按需标注，
               或仅出「规则候选清单」不生成卡片，人工勾选后再深标
```

**预算控制**：云端 Provider 路径按项目设 token 预算（按报价档位配置），预算耗尽后剩余逻辑标记「未覆盖」，进入下一批扫描/升级套餐处理，进度对客户可见。Agent Native 不计入该预算，但仍必须报告导出证据的覆盖范围和待确认项。

**采样与模式去重**：
- 结构相同的 if/校验模式（如 N 个模块的同款"金额不为负"校验）只标注一次，其余以「同款规则 × N」引用；
- 相同 SQL 结构（仅表名/字段不同）归并为模板标注。

**增量扫描（200 万+ 第二次起）**：基于 `git diff` 只重扫变更文件及其直接调用者（影响面传播一层），未变更部分沿用上版图谱（`scans` 版本化天然支持）。

### 9.7 执行编排（Celery）

```text
探测 → 档位判定 → [单机全量] 或 [L0符号索引 → 分片方案生成 → 分片解析(并行) → 合并 → L1 → L2(优先级队列) → L3]
     ↑___________________________ 任意阶段失败：按片/按任务重试续扫 ________________________________|
```

- `scan_tasks` 表驱动：每行 = 一个可独立执行的最小任务（片 × stage），状态机：pending → running → done/failed → retry；
- 同一项目内任务按依赖排序（L0 → 分片解析 → 合并 → 语义分层），跨项目可同时跑多个扫描；
- worker 无状态，任务失败自动重试（幂等落库），支持多节点扩展。

### 9.8 决策表与资源预估（含 200 万+）

| 档位 | 规模 | 分片 | 解析+合并 | L1/L2 语义 | 首次全流程 | 建议资源 |
|---|---|---|---|---|---|---|
| S | ≤10 万行 | 0 | 5-15 分钟 | 0.5-1 小时 | ≤1 小时 | 16GB 单机 |
| M | 10-100 万行 | 0 | 10-40 分钟 | 2-4 小时 | 2-4 小时 | 32GB 单机 |
| L | 100-200 万行 | 4-8 片 | 30-90 分钟 | 4-8 小时 | 6-10 小时 | 扫描机 32-64GB |
| XL | 200 万+ | 8-16 片 | 1-2 小时 | 核心 4-8 小时（分批） | 核心 8 小时内，全量 1-2 天 | 扫描机 64GB+ / 多节点 |

### 9.9 云端 LLM 成本估算（以 DeepSeek 档位举例）

| 规模 | 规则候选 | 采样+去重后卡片 | 输入 token | 参考成本 |
|---|---|---|---|---|
| ≤100 万行 | 1-2 万条 | 1-2 万 | ~3000 万 | 数十-百元级 |
| 100-200 万行 | 3-5 万条 | 2-3 万 | ~8000 万 | 百元级 |
| 200 万+ | 10-15 万条 | 3-5 万 | ~1.5 亿 | 数百元级（预算制） |

成本不是主要瓶颈（云 API 足够便宜），**时长与质量确认**才是；因此大规模不做"少标"，而做"优先级+分批"。

### 9.10 超大项目（200 万+）注意事项

- **巨型生成文件**（单文件 >2 万行，代码生成器产物）：独立成片、单独标记，不进主链语义分析；
- **无包名/默认包文件**：按目录分组归属；
- **前端代码**：单独一片（Vue 分析独立于 Java 分片），前端行数不计入 Java 分片预算；
- **内存不足降级**：自动减并发（16→8 worker）、合并小片、转入 L0/L1 分层模式，保证全景先出；
- **多模块仓库**（聚合工程）：按顶层包 = 模块边界天然分片，模块级粒度优先于行数均衡；
- **分批交付承诺**：XL 档向客户承诺"首次扫描 8 小时内出全景+核心业务"，全量成果按业务域分批交付。

### 9.11 非标准技术栈的最小适配（规模化验证样本 n-s-modernization-origin 专用）

该样本为富士通 APWORKS/JBK 老式批处理 + GUI 系统（无 Spring/MyBatis/Vue，Shift-JIS 编码），作为**规模化验证样本**，MVP 对它的承诺范围：

| 能力 | 在本样本上的状态 |
|---|---|
| 探测 / 分片 / 并行解析 / 合并 / 分层调度 | **完整支持**（规模化引擎全链路验证） |
| 模块地图 | 支持：125 个程序模块 + common 公共库，按包分片 |
| 批处理清单 | 支持：main 入口识别（1,221 个）+ 入口→调用链追踪 |
| 字符串 SQL 提取 | 支持：字符串常量中 SQL 文本提取 + SQLGlot 解析 + 调用点关联（262 文件） |
| 规则候选 | 支持：if/校验/异常/金额运算（与 Spring 项目同机制） |
| API 业务链路 | 不适用（无 HTTP API，不产出） |
| 前端映射 | 不适用（无 Web 前端，不产出） |
| 语义层深度 | 降级：仅 L1 骨架（域/对象）+ 批处理清单说明；L2 规则卡片只标核心批处理 |
| 增量扫描 | 不可用（无 git 历史），按目录快照全量 |

**适配工作量**：仅入口识别（main/事件监听）与字符串 SQL 提取两个解析器扩展，不投入 APWORKS 框架语义解析。

**该样本的核心价值**：验证 247 万行（XL 档）的真实执行——探测秒级、L0 符号索引分钟级、分片解析/合并时长、内存峰值、单机 64GB 可行性，校准 9.8 决策表。

---

## 10. 部署形态

| 阶段 | 形态 |
|---|---|
| 开发期 | docker compose：PG + Redis + 后端 + 前端 + worker（扫描器本地 CLI 运行） |
| 交付期（默认） | 客户内网私有化：docker compose 单机（≤100 万行）或加 worker 节点（100 万+，扫描机 64GB 级） |
| 安全底线 | 源码不出内网；仅上传结构化图谱；支持敏感字段脱敏（表名/字段名映射）；图谱加密存储；不用客户代码训练公共模型 |

---

## 11. MVP 范围声明（技术侧）

**做**：Java Spring Boot + Vue + MySQL 项目的全量确定性分析、核心语义标注、规则卡片、业务地图、API 链路、数据血缘（方法级）、可追溯问答、人工确认、Markdown/Word 文档、版本对比（手动触发）、**规模化执行引擎：秒级规模探测 → 自适应档位判定 → 分片并行（支持 200 万行+）→ 分层语义推进 + 增量扫描**。

**不做（MVP 内）**：运行时分析（OpenTelemetry/日志接入）、自动 BPMN、代码生成、多语言解析、SaaS 多租户、完整分布式集群（多机扩展能力预留，单机 64GB 内先跑通）、向量库检索为主（图谱优先）。

---

## 12. 待技术验证项（M0 阶段必须出结论）

1. tree-sitter-java 对若依这类注解密集项目的解析完整性（方法定位、注解取值）；
2. SQLGlot 对 MyBatis XML 动态 SQL（`<if>/<foreach>/<choose>`）的解析效果；
3. Vue 前端到后端 API 的自动映射命中率（决定前端分析深度）；
4. **200 万+ 行样本的实测基准**：L0 符号索引耗时、分片解析吞吐（行/秒/worker）、合并耗时、单机内存上限（校准 9.8 估算表）；
5. OpenAI Compatible 与 Anthropic Native 的结构化 JSON 输出稳定性、重试成本与失败率；Agent Native 导入的证据校验拒绝率与覆盖率；
6. 200 万+ 行开源/自构验收样本的选型（见 mvp-task-split M0-1）。
