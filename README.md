# Code2Business（C2B）—— 从遗留代码还原业务知识

> 当前正式版本：[**v0.6.1**](https://github.com/imlao17/Code2Business/releases/tag/v0.6.1)（2026-09-26）。
> 本版统一了 CLI/MCP 的默认数据库到 `~/.c2b/c2b.db`，不再在工作目录遗留 `c2b.db` 与中间产物。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)，本版细节见 [v0.6.1 发布说明](docs/releases/v0.6.1.md)。

Code2Business 把旧系统代码转换为可追溯、可确认、可交付的业务资产：

```text
代码仓库 → 技术图谱 → 业务域 / 对象 / 规则 / 流程 → 运行验证 + 人工确认 → 需求与交付文档
```

## 它解决什么问题

不是"让 AI 解释一段代码"，而是四个更完整的场景：

- 接手遗留系统时，不知道系统有哪些模块、入口、表和调用关系；
- 重构、替换、迁移前，缺少可追溯的业务规则、数据影响与模块职责；
- 产品、实施和客户需要业务文档，但知识只藏在代码和少数开发人员脑中；
- 希望 AI 加速理解，但不接受没有代码证据、无法人工核实的结论。

工作方式是：先用确定性扫描建立代码证据，再由 AI 做语义归纳；所有正式交付内容都以**人工确认**作为可信边界。

## 当前定位与质量口径

- **本地单实例、单团队试点工具**：不内置登录、角色权限或跨客户访问隔离。Web 默认部署在 `localhost` 或可信内网；开放给多人或公网前，必须由外部反向代理提供身份认证与访问边界。
- **已验证样本**：两个固定样本——RuoYi-Vue（Spring Boot，64,975 行）与富士通 APWORKS/JBK（247 万行老式 Java）。质量口径与未覆盖能力见[质量基线](docs/quality-baseline.md)和[RuoYi-Vue 验证报告](docs/validation/ruoyi-vue-2026-08.md)。
- **语义准确率的说法有边界**：RuoYi-Vue 固定提交的首轮独立语义抽样为 50 条，47 条正确、3 条部分正确（严格正确率 94%）。这是该样本和该生成批次的审计结果，不是跨项目通用准确率。

## 三个独立产品

本仓库包含 **三个可以分别安装、部署和使用的产品**。它们共享同一套 `core/` 引擎和数据模型，但不要求彼此同时存在。

| 产品 | 适合谁 | 解决的问题 | 独立运行条件 | 入口 |
|---|---|---|---|---|
| **① C2B 工作台** | 产品经理、业务分析师、测试、交付顾问 | 可视化图谱、人工确认、R0-R3 运行证据管理、单团队评审与导出 | 浏览器版用 Docker；macOS 本地版不需要 Docker | [Web 工作台](web/README.md) / [macOS Desktop](desktop/README.md) |
| **② C2B CLI** | 开发、架构、运维、CI/批处理使用者 | 在服务器或脚本中批量扫描、生成规格、导出文档、接入自动化；含 MCP 服务器供 AI 客户端调用 | Python 3.11+；可选 PostgreSQL；语义可选外部 LLM 或 Agent Native 导入 | [cli/README.md](cli/README.md) |
| **③ C2B Agent Skills** | 使用 Claude Code、Codex、opencode 等 Agent 的团队 | 用自然语言编排扫描、语义分析和文档交付，保留每阶段过程与证据 | Python 3.11+、pip、兼容的 Agent 宿主 | [skill/README.md](skill/README.md) |

`core/` 是三种产品共用的实现和运行时，不是第四个面向用户的部署产品。Web 镜像、CLI wheel、Agent Skill 与 macOS Desktop 都会携带或安装它。macOS Desktop 是产品①的本地优先分发形态，不是一套重复的业务内核。

### 如何选择

```text
需要业务人员在浏览器里看图谱、确认规则、下载文档？ → 选 Web 工作台
希望在 Mac 上双击即用、直接选择本地代码目录？      → 选 C2B Desktop
需要在服务器、终端、CI 或脚本中稳定批处理？       → 选 CLI
希望直接对 AI Agent 说“分析这个仓库并交付文档”？     → 选 Agent Skills
```

## 数据存放与清理（v0.6.1 起）

CLI、Agent Skills 与 MCP 共用同一个默认数据库，分析数据**不再写进你的工作目录**：

| 路径 | 内容 | 说明 |
|---|---|---|
| `~/.c2b/c2b.db` | 统一数据库（SQLite） | CLI/Skills/MCP 默认；`C2B_DB_URL` 可改指 PostgreSQL |
| `~/.c2b/tmp/` | 中间产物（证据/语义 JSON） | 导入成功后自动删除 |
| `~/.c2b/llm_cache/` | LLM 结果缓存 | 上限 200MB（`C2B_LLM_CACHE_MAX_MB`），超限淘汰最旧 |
| `~/.c2b/venv/` | Agent Skills 安装的运行时 | 由 `skill/install.sh` 创建 |
| Web 工作台 | Docker 内 PostgreSQL | 容器私有，不对宿主机暴露端口；跨产品共用见 [web/README.md](web/README.md) |
| macOS Desktop | Application Support | 本地优先，详见 [desktop/README.md](desktop/README.md) |

发现旧版本在工作目录遗留的 `c2b.db` / `c2b-evidence.json` 等文件时，在对应目录执行：

```bash
c2b clean --yes          # 删除当前目录的旧版残留；--cache/--tmp 可选清空缓存与中间产物
```

跨产品数据互通：把 CLI/Skill 与 Web 都配置为同一个 PostgreSQL 地址（`C2B_DB_URL`）即可；Web 默认内置的 PostgreSQL 容器不对宿主机暴露端口，共用时建议使用单独托管的 PostgreSQL。

## 三种产品的快速使用

### ① C2B Web 工作台：面向可视化协作与交付

**适用场景**：需要把遗留系统分析结果展示给产品、实施、业务方或客户；需要人工确认业务规则、处理待确认项并下载交付文档。

```bash
cd web/deploy
./up.sh  # 首次启动会创建私有 .env 并生成数据库密码
# 打开 http://localhost:8080
```

启动后，在网页中依次完成：

1. 上传 ZIP，或输入 Git 地址创建项目；Git 导入完成克隆后再继续；
2. 检查代码来源和项目名称，点击"开始扫描"构建确定性代码图谱；
3. 扫描完成后点击"运行标注"，生成业务域、对象、规则、流程和业务推演；
4. 在"流程与规则"页确认、修改或废弃规则；
5. 对规则、流程或业务推演录入测试、受控环境或生产环境的运行证据；
6. 在"文档与验收"页下载正式文档、草稿、Excel 或模块规格 ZIP。

两个分析层次需要区分：**开始扫描**结束后即可看到模块、代码对象、调用关系、数据表和 SQL 等确定性图谱；只有完成下一步的**运行标注**，业务域、业务对象、规则和流程等语义结果才会出现。Web 端没有交互式 Agent 会话，因此"运行标注"需要先配置云端模型 Provider；若不配置模型，也可以只使用扫描图谱和相关导出能力。

Web 也支持完全离线交付：构建机执行 `./build-dist.sh`，目标机器只需 Docker 即可加载镜像包运行。离线目标机先复制 `.env.example` 为私有 `.env` 并设置密码；如果使用内网模型，需在可信网络中显式设置 `C2B_ALLOW_PRIVATE_LLM=1`。详见 [Web 工作台说明](web/README.md) 与 [离线部署说明](web/deploy/README.md)。

### ①a C2B Desktop（macOS）：面向本地、离线与即开即用

**适用场景**：不希望安装 Docker，也不想先部署服务器；在个人 Mac、客户现场或内网笔记本上直接选择一个源码目录开始业务还原。

Desktop 使用与 Web 工作台相同的界面和 Python Core，但在本机以原生 `.app` 运行：默认 SQLite、本地目录选择器、本地任务进程和 `127.0.0.1` API，不提供登录也不会开放网络端口。通过"本地代码目录"导入时只关联原目录，不复制或删除原始代码；模型 Key 保存在当前用户的 macOS 钥匙串。构建、安装、签名公证、数据位置、隐私边界和当前限制见 [C2B Desktop（macOS）说明](desktop/README.md)。

### ② C2B CLI：面向自动化、服务器和批处理

**适用场景**：希望把分析纳入 CI、夜间任务、脚本管线，或在没有浏览器界面的服务器上完成扫描与交付。

```bash
# 在源码仓库构建并安装
./cli/build-wheel.sh
python3 -m pip install cli/dist/c2b-*.whl

# 初始化数据库后先完成确定性扫描（数据落 ~/.c2b/c2b.db）
c2b init
c2b scan /path/to/legacy-code --name legacy-system

# 语义方案 A：云端 Provider（配置 OpenAI Compatible 或 Anthropic Native 后运行）
c2b label 1 --limit 200

# 语义方案 B：当前 Agent（无需在 C2B 配置外部 Key；中间产物自动放 ~/.c2b/tmp）
c2b export-evidence 1
c2b agent-schema > ~/.c2b/tmp/c2b-semantic-result.json
# 让当前 Agent 根据源码和 ~/.c2b/tmp/c2b-evidence.json 填写语义结果
c2b import-semantic 1   # 校验导入成功后自动清理临时文件

# 交付文档（落当前目录，路径可自定）
c2b module-specs 1      # 模块规格（需云端 Provider）
c2b docs 1 --type overview --output 系统现状说明书.md
c2b docs 1 --type rules --output 业务规则清单.md
```

CLI 的确定性扫描可独立完成。云端语义支持 OpenAI Compatible 与 Anthropic 原生 API；若正通过 Codex、Claude Code 等 Agent 工作，可用 Agent Native 的证据导出/结构化导入完成业务域、对象、规则和流程，不需在 C2B 中再配置外部模型 Key。模块规格和 QA 仍是独立 LLM 能力。

**MCP 接入（v0.5+）**：`c2b mcp` 以 stdio 暴露 8 个工具（扫描、状态、规则、文档、证据导出、语义导入等），支持 MCP 的 AI 客户端（ZCode、Claude Desktop、Codex 等）可直接调用，与 CLI/Skills 使用同一数据库。配置示例见 [skill/README.md](skill/README.md)。

完整命令、配置和 CI 使用方式见 [CLI 说明](cli/README.md)。

### ③ C2B Agent Skills：面向自然语言编排

**适用场景**：团队已经使用 AI 编程 Agent，希望通过一句话完成分析，又需要 Agent 在每个阶段说明覆盖范围、证据和待确认项。

```bash
cd skill
./install.sh
export PATH="$HOME/.c2b/venv/bin:$PATH"
```

随后在兼容的 Agent 中直接说：

```text
用 c2b 分析 /path/to/legacy-code，先给我规模报告，再生成业务规则和系统现状说明书。
```

技能默认让当前 Agent 通过"扫描证据 → 结构化业务结论 → 校验入库"完成语义阶段，不会要求重复配置外部模型 Key；如需无人值守批量运行，也可显式改用云端 Provider。对于中小仓库，还可使用零依赖参考版进行规模探测和辅助分析。完整安装方式、宿主要求和使用边界见 [Agent Skills 说明](skill/README.md)。

## 共同工作原理

```mermaid
flowchart LR
    A["导入代码：目录 / ZIP / Git"] --> B["规模探测与安全校验"]
    B --> C["Java 代码 + MyBatis XML/SQL 解析"]
    C --> D["技术图谱：模块、方法、调用、SQL、表"]
    D --> E["AI 语义：业务域、对象、规则、流程"]
    E --> F["人工确认与审计"]
    E --> I["可选运行验证：R0-R3"]
    F --> G["正式文档与验收材料"]
    I --> G
    E --> H["草稿、业务推演、模块规格、QA"]
```

- **代码证据优先**：每条规则和结论都尽量关联文件、行号、表或调用链；
- **AI / Agent 只做语义归纳**：云端模型或当前 Agent 的结果先进入待确认知识，而不是直接成为正式结论；
- **正式/草稿分离**：正式文档只纳入人工确认的业务知识；草稿才包含未确认 AI 项和业务主线；
- **三条信任轴不混算**：V/代码定位表示静态证据，R0-R3 表示运行验证，人工确认状态单独保存；确认或录入运行证据都不会改写 AI 可信度；
- **可追溯**：规则确认、修改、废弃都会留下审计记录。

## 仓库结构

```text
├── core/              # 共享 Python 引擎：扫描、图谱、语义、API、文档、测试
├── web/               # 产品①：Vue 工作台与 Docker/离线部署
├── desktop/           # 产品①：macOS 原生 Desktop（SwiftUI + Core sidecar）
├── cli/               # 产品②：CLI wheel 与构建脚本
├── skill/             # 产品③：Agent Skills 与安装脚本
└── docs/              # PRD、架构、路线图、质量基线与验证报告
```

## 开发与验证

GitHub Actions 会在每个 PR 和 `main` 提交上自动执行后端回归、前端生产构建、wheel/文档检查，以及真实 Docker Compose + PostgreSQL 的导入、扫描、API 重启持久化和删除冒烟测试。本地可复跑：

```bash
# 后端回归测试（104 项）
PYTHONPATH=core/src python -m pytest core/tests -v

# 前端生产构建
cd web/frontend && pnpm install --frozen-lockfile && pnpm build

# 固定 Spring/MyBatis 样本的确定性图谱回归（先按质量基线准备 project_id）
PYTHONPATH=core/src python -m c2b.cli evaluate <project-id> \
  --manifest docs/quality-fixtures/ruoyi-vue-2026-08.json

# 构建统一 wheel（同步到 CLI、Skill 与 Web 部署目录）
cd ../.. && ./cli/build-wheel.sh --copy-to web/deploy/dist
```

更多设计细节见：[产品需求文档](docs/PRD.md)、[技术架构](docs/architecture.md)、[三产品整体设计 HTML](docs/Code2Business_三产品整体设计方案_2026-08-08.html)、[运行证据设计](docs/runtime-evidence.md)、[质量基线](docs/quality-baseline.md)、[RuoYi-Vue 验证报告](docs/validation/ruoyi-vue-2026-08.md)、[实施路线图](docs/roadmap.md)。

## 安全与使用边界

- 不要提交真实客户代码、数据库、LLM Key、运行时缓存或部署环境文件；这些均已列入 `.gitignore`；
- Web 默认绑定本机地址。远程访问请放在 HTTPS、身份认证和反向代理之后；
- Git/LLM 地址默认只允许公网 HTTPS。连接内网 Git 或本地 LLM 时需要显式放宽安全开关，且只应在可信网络中使用；
- 当前深度代码图谱以 Java 为主；其他语言可被探测，但尚未具备同等级语义解析能力；
- Spring MVC 路径与 MyBatis XML/SQL 已有固定样本回归；当前可手工记录 R1 测试、R2 受控/预发布、R3 生产运行证据，但不会自动采集 OpenTelemetry、日志或 Trace。Mapper XML 与 Java Mapper 方法的精确绑定、Vue→API 映射和自动运行链路采集仍未实现。

## License

许可证待项目维护者最终确定后补充。
