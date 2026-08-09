# core —— C2B 共享引擎（非独立产品）

> 当前分发版本：v0.2.1。完整变化见[仓库变更日志](../CHANGELOG.md)。

`core/` 是三个 C2B 产品共用的 Python 实现：扫描器（确定性分析）、AI 语义层、文档引擎、Web API 与 CLI 入口。它不是第四个面向用户部署的产品：Web 部署会打包它，CLI 和 Agent Skills 会安装其 wheel。

如果你只想使用 C2B，请从 [Web 工作台](../web/README.md)、[CLI](../cli/README.md) 或 [Agent Skills](../skill/README.md) 中选择一种形态；只有二次开发、调试 API 或贡献代码时才需要直接操作 `core/`。

## 是什么

| 模块 | 职责 | 技术 |
|---|---|---|
| `scanner/probe.py` | 规模探测：档位判定（S/M/L/XL）/编码检测/历史版本识别 | 纯标准库，秒级 |
| `scanner/parse.py` | tree-sitter Java 解析 + MyBatis XML / `.sql` 语句级提取：类/方法/调用/API/SQL 候选 | tree-sitter-java |
| `scanner/sqlx.py` | SQL 提取与解析：表×字段×读写 | SQLGlot |
| `scanner/graph.py` | 图谱落库编排：分片/合并/统计 | SQLAlchemy |
| `semantics/` | AI 语义层：S1 域 / S2 对象 / S3 规则 / S4 流程 / S6 推演 / 模块规格 / 问答 | LLM + 证据链 |
| `llm.py` | LLM 适配：OpenAI Compatible / Anthropic Native + 缓存 + 成本计数 + mock | httpx |
| `semantics/agent_native.py` | 当前 Agent 的证据导出、结构化校验与语义入库 | 无外部模型 |
| `documents.py` | 6 类交付文档生成（Markdown/Word） | Jinja2/python-docx |
| `runtime_evidence.py` | R0-R3 运行证据聚合、导出与语义重跑清理 | SQLAlchemy |
| `api/` | FastAPI 查询与操作 API | FastAPI |
| `cli.py` | 命令行入口（所有能力） | click |

## 必需底层服务

| 服务 | 必须 | 说明 |
|---|---|---|
| Python | ✅ 3.11+ | 运行时 |
| PostgreSQL 16 | 推荐 | 图谱/业务知识存储；开发可用 SQLite（零配置，单写者限制） |
| LLM API | 可选 | OpenAI Compatible 或 Anthropic Native（云端语义、模块规格、QA） |
| Node.js | web 形态需要 | 前端构建（18+） |
| Docker | web 形态需要 | 一键部署（compose） |

## 安装与引用

```bash
# 开发/源码引用（web 形态）：
pip install -e core/          # editable，改代码即生效

# 构建分发物（cli/skill 形态用）：
cd core && python -m build --wheel   # 产出 core/dist/*.whl
# 或：cli/build-wheel.sh 一键构建
```

## 云端 LLM 配置（Web / CLI 可选）

```json
// ~/.c2b/config.json
{ "llm_provider": "openai_compatible",
  "llm_base_url": "https://api.deepseek.com/v1",
  "llm_api_key": "sk-xxx",
  "llm_model": "deepseek-chat",
  "llm_mode": "api" }        // mock = 不调 API，管线验证用
```

`llm_provider` 可选 `openai_compatible`（默认，地址须实现 `/chat/completions`）或
`anthropic`（原生 Claude Messages API，地址通常为 `https://api.anthropic.com`）。环境变量同样支持
`C2B_LLM_PROVIDER`、`C2B_LLM_BASE_URL`、`C2B_LLM_API_KEY`、`C2B_LLM_MODEL`。

Agent Skills 不需要配置这份文件来生成首轮业务语义：它使用当前 Agent 读代码，调用
`export-evidence` 和 `import-semantic` 将带行号证据的结构化结论校验后入库。此路径不替代
`module-specs` 和 `qa` 的云端模型调用。

安全默认值：LLM 地址只允许公网 HTTPS。连接本机 Ollama 等私网服务时需显式设置
`C2B_ALLOW_PRIVATE_LLM=1`；这会放宽出站网络边界，只应在可信内网开启。

## 数据库配置

```bash
# PostgreSQL（推荐）
export C2B_DB_URL="postgresql+psycopg://user:pass@localhost:5432/c2b"
# SQLite（开发）
export C2B_DB_URL="sqlite:///./c2b.db"

c2b init                      # 建表
```

## 数据模型（核心表）

```
projects → scans → modules / code_objects / call_edges / data_tables / sql_operations
                → business_domains / business_objects / business_rules / business_flows
                → business_chains / runtime_evidence / module_analyses / questions / confirmations / qa_records
```

## 命令行（cli/ 与 skill/ 都调用它）

```bash
c2b scan <目录> --name <项目>      # 扫描：代码 → 代码图谱
c2b status <project_id>            # 流程状态
c2b evaluate <project_id> --manifest quality.json  # 已知代码事实的图谱回归
c2b label <project_id> --limit N   # AI 标注：域/对象/规则/流程/推演
c2b export-evidence <project_id> --output evidence.json  # Agent Native：导出图谱证据
c2b agent-schema > semantic.json                          # 输出/填写结构化模板
c2b import-semantic <project_id> --input semantic.json    # 校验行号证据后原子入库
c2b module-specs <project_id>      # 模块需求规格
c2b docs <project_id> --type X     # 交付文档（overview/rules/...）
# 正式文档只纳入人工确认的业务知识；--draft 才包含 AI 未确认项与 AI 主线
c2b export-specs <project_id>      # 模块规格 zip
c2b qa <project_id> "问题"         # AI 问答
```

## 回归验证

```bash
PYTHONPATH=core/src python -m unittest discover -s core/tests -v
```

测试覆盖扫描调用解析、分片任务、编码抽样、项目隔离、确认审计、R0-R3 运行证据、重标数据保护、
文档信任边界、上传/出站安全、Spring Mapping / MyBatis XML 图谱与 probe-only 接续流程。

固定的 RuoYi-Vue 图谱回归清单、人工语义评审模板和当前结果见
[../docs/quality-baseline.md](../docs/quality-baseline.md)。这里的 `evaluate` 只验证确定性代码事实；
业务规则的正确性必须由独立人工抽样评审，不能从模型输出本身推导准确率。

运行证据当前通过 Web/API 录入，支持规则、流程和业务推演：R1=测试执行，R2=受控/预发布环境，
R3=生产环境，未录入时派生为 R0。它与静态证据、AI 可信度和人工确认状态分别保存；当前不会
自动接入 OpenTelemetry、日志平台或 APM。
