# C2B Web 工作台（产品①）

> 当前正式版本：[v0.4.0](https://github.com/imlao17/Code2Business/releases/tag/v0.4.0)。完整变化见[仓库变更日志](../CHANGELOG.md)。

这是面向本地单团队评审和交付演示的独立 Web 产品：项目导入、扫描、AI 标注、业务图谱、模块需求规格、业务推演、人工确认、AI 问答与文档导出都在浏览器内完成。

**它解决什么问题？** 当遗留系统的知识只在代码和少数开发人员脑中，而产品、实施或客户需要看得懂、能核实、可下载的业务材料时，使用 Web 工作台。

它可以独立部署，**不需要额外安装 CLI 或 Agent Skills**。Docker 部署包会自行构建和运行后端、数据库及前端；CLI/Skill 只是可选的自动化入口。三种产品的选择方式见根目录 [README](../README.md)。

若你的目标是个人 Mac 的本地分析而不是浏览器/服务器部署，请使用同一工作台的 [C2B Desktop](../desktop/README.md)：它不需要 Docker、默认 SQLite，并可通过原生目录选择器直接关联本地代码。Desktop 不改变 Web 的团队部署职责。

**当前访问边界**：本版本不提供 Web 登录、角色权限或多客户访问隔离。默认部署仅监听本机地址，适合单人或可信本地团队使用；远程访问必须由部署方在反向代理层提供 HTTPS 与身份认证，且不应把不同客户的数据放进同一个无隔离实例。

## 适用场景

- 产品经理或实施顾问需要快速理解并展示遗留系统；
- 业务方需要逐条确认、修改或废弃 AI 识别出的规则；
- 项目需要输出系统现状、功能清单、规则清单、数据字典、接口清单和验收材料；
- 客户现场或内网环境希望一套浏览器可访问的私有化工作台。

## 快速部署（Docker 一键）

```bash
cd web/deploy
./up.sh                       # 构建前端 + core wheel + 后端镜像 + 启动（PG + API + 前端）
# 打开 http://localhost:8080
# 右上角「LLM 设置」选择 OpenAI Compatible（含自建兼容端）或 Anthropic Native，填入地址、模型和 Key
```

停止：`./down.sh`（数据保留在 volume）。

### 独立分发（离线部署）

```bash
./build-dist.sh               # 产出离线包 c2b-web-deploy.tar.gz
# 目标机器（仅 Docker）：
tar xzf c2b-web-deploy.tar.gz
cd c2b-web-deploy
shasum -a 256 -c MANIFEST.sha256    # Linux 可改用 sha256sum -c
docker load < c2b-web-images.tar.gz
cp .env.example .env && chmod 600 .env
# 编辑 .env，替换 C2B_POSTGRES_PASSWORD；远程部署还应设置 C2B_API_TOKEN
docker compose up -d
```

离线包内已包含：
- `frontend-dist/` 静态站点
- `dist/c2b-*.whl` API 安装包
- `c2b-web-images.tar.gz` 运行时镜像包（API + PostgreSQL + nginx）
- `docker-compose.yml` / `Dockerfile` / `nginx/`
- `MANIFEST.sha256` 校验清单

## 本地开发

```bash
# 后端（依赖 core/）
pip install -e ../core
export C2B_DB_URL="postgresql+psycopg://user:pass@localhost:5432/c2b"
uvicorn c2b.api.main:app --port 8123

# 前端
cd frontend && pnpm install --frozen-lockfile && pnpm dev --host 127.0.0.1
# http://localhost:5173
```

## 使用流程（网页内完成全流程）

1. **新建项目**：上传 zip 代码包，或填写 Git 地址；Git 克隆完成后项目进入“待扫描”
2. **开始扫描**：在顶部流程条或项目总览点击“开始扫描”，构建确定性代码图谱（异步 + 进度）
3. **运行标注**：扫描完成后，AI 识别业务域/对象/规则/流程/推演（异步 + 进度）
4. **模块需求规格**：批量分析全部模块 → 查看/下载/打包
5. **人工确认**：规则页逐条/批量确认、修改或废弃；处理待确认项
6. **导出**：下载 6 类交付文档、模块规格 ZIP 和规则 Excel

导入阶段只负责把代码安全地放入项目目录；扫描是显式操作。这样可以在消耗扫描资源前检查上传是否正确，也避免 Git 克隆、解压失败与图谱扫描混成一条难以定位的问题链路。

正式文档只纳入人工确认的业务知识；在内部评审阶段可切换为草稿，查看 AI 未确认项和业务主线。

顶部流程引导条实时显示当前进度与下一步。

## 模型 Provider

Web 工作台的后台任务没有“当前 Agent 会话”，因此只提供可由服务端稳定调用的云端 Provider：

- **OpenAI Compatible**：适用于 OpenAI、DeepSeek、Kimi、Ollama 和自建网关；自定义地址必须实现 `POST /chat/completions` 协议。
- **Anthropic Native**：适用于 Claude 原生 Messages API，地址通常为 `https://api.anthropic.com`。

两种 Provider 都会经由相同的超时、重试、缓存、JSON 校验和出站 URL 安全限制。Web 不会自动猜测任意自定义 API 的协议；请按实际协议选择 Provider。

若模型运行在 Docker、宿主机或局域网中，默认安全策略会拒绝私网地址。在可信内网把 `web/deploy/.env` 的 `C2B_ALLOW_PRIVATE_LLM=1`，执行 `docker compose up -d --force-recreate api` 后再保存模型地址。Docker 同网络服务使用服务名，例如 `http://ollama:11434/v1`；Docker Desktop 上的宿主机服务通常使用 `http://host.docker.internal:11434/v1`。详见 [部署说明](deploy/README.md)。

## 页面

| 页面 | 功能 |
|---|---|
| 项目总览 | 统计看板、模块 Top、规则分布、可信度分布 |
| 模块需求规格 | 搜索/批量/单模块分析、规格查看、zip 打包 |
| 业务对象与流程 | 对象（表/动作）、流程步骤、流程 R0-R3 运行证据 |
| 业务推演 | 驱动关系图（V1-V4 为**静态**证据等级；V0 移入推断假设）、主线、设计叙事、整体 R0-R3 运行证据 |
| 流程与规则 | 规则卡片、筛选、确认/废弃、批量确认、R0-R3 运行证据、Excel 导出 |
| 数据与接口 | 数据表、SQL 读写场景 |
| 待确认项 | 待处理清单、标记解决 |
| 文档与验收 | 6 类交付文档、模块规格下载 |
| AI 问答 | 全局浮窗、历史持久化 |

### 运行验证口径

- `R0`：没有运行证据（系统自动派生，不需要录入）；
- `R1`：测试执行证据；
- `R2`：受控环境或预发布环境的观测证据；
- `R3`：生产环境的观测或数据核对证据。

每条记录保存环境、类型、摘要、测试名/日志条件/Trace ID 等引用、观测时间和记录人。R 等级只取
当前目标已有记录中的最高等级，不会修改 AI 可信度、V 静态证据等级或人工确认状态。当前版本是
本地人工录入和审计管理，不会自动连接 OpenTelemetry、APM 或日志平台。
完整的数据结构、API、重跑清理和导出规则见[运行证据设计](../docs/runtime-evidence.md)。

## 与 CLI / Agent Skills 共享数据（可选）

Web 默认使用自己的 PostgreSQL 数据卷，因此可以完全独立运行。若希望 CLI 或 Agent Skill 的扫描结果也显示在工作台中，请让三者使用同一个共享 PostgreSQL 地址；默认 compose 内置的 PostgreSQL 不向宿主机暴露端口，跨产品共用时建议使用单独托管的 PostgreSQL，或由维护者显式配置受保护的数据库网络。这是一种可选集成，不是部署前置条件。

固定 RuoYi-Vue 对 Spring MVC/MyBatis 的图谱验证与质量评审方式见根目录 [质量基线](../docs/quality-baseline.md)。首轮 50 条独立语义抽样为 47 条正确、3 条部分正确；这个数字只代表归档的固定批次。Web 中每个新项目的 AI 业务卡片仍需人工抽样确认，不能复用图谱回归通过率或若依百分比替代项目验收。

## 目录

```
web/
├── frontend/         # Vue3 + Element Plus + ECharts
└── deploy/           # Dockerfile / compose / nginx / up.sh / down.sh / build-dist.sh
                    # 运行时会生成 frontend-dist/ 与 dist/ 作为自包含构建产物
```
