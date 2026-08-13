# C2B Desktop（macOS 本地工作台）

> 当前开发版本：v0.3.0。C2B Desktop 是 Web 工作台的本地优先分发形态，不是第四套业务引擎。

它面向个人架构师、业务分析师和现场交付人员：双击启动后选择本地代码目录，直接完成代码扫描、图谱查看、
业务标注、人工确认和文档导出。**不需要登录、不需要 Docker、默认不需要 PostgreSQL。**

## 使用方式

1. 打开 `C2B Desktop.app`；
2. 点击“新建项目” → “本地代码目录” → 选择需要分析的仓库；
3. 确认项目名称，点击“关联本地目录”；
4. 在总览点击“开始扫描”；扫描完成后可查看模块、调用、SQL、表等确定性图谱；
5. 如需业务域、规则和流程，在右上角配置 LLM 后执行“运行标注”；
6. 在规则页人工确认后，从“文档与验收”导出交付物。

“扫描”与“运行标注”是两个独立阶段：扫描不依赖模型，标注才会调用云端模型或本地兼容模型。

## 本地数据与隐私

- 项目数据库、C2B 设置、缓存与托管导入内容保存在 macOS 的 `Application Support/C2B Desktop/`；
- 通过“本地代码目录”关联的项目**不会复制源码**，只保存目录引用；删除项目不会删除用户原始代码目录；
- 内置 API 只监听 `127.0.0.1:8123`，不开放网络端口；
- Desktop 可连接 `localhost` / 局域网中的 OpenAI Compatible 本地模型（例如 Ollama）；这项放行仅在本地桌面进程中生效，不改变 Web 服务端的 SSRF 防护；
- 当前 LLM 设置沿用 C2B 本地受限配置文件（权限为仅当前用户）；macOS Keychain 集成计划在后续版本加入，因此不应把多人共用的 macOS 账户当作安全隔离边界。

## 开发运行

前置条件：macOS 11+、Xcode Command Line Tools、Rust、Node.js 20+、pnpm、Python 3.11+。Python 环境必须已安装
`core/pyproject.toml` 所列依赖；仓库根目录的 `.venv` 是默认开发运行时。

```bash
cd desktop
pnpm install
pnpm dev
```

启动时桌面壳会：

1. 启动现有 Vue/Vite 前端；
2. 启动同一仓库的 FastAPI/Python Core；
3. 将本地数据库、源码托管区和设置定向到应用数据目录；
4. 等待本地 API 健康检查通过后打开窗口。

若 Python 不在仓库 `.venv/bin/python`，启动前设置：

```bash
export C2B_DESKTOP_PYTHON=/absolute/path/to/python
pnpm dev
```

## 生成可分发的 `.app`

```bash
# 只需在构建机安装一次
../.venv/bin/python -m pip install pyinstaller

cd desktop
pnpm install
pnpm build
```

构建脚本会用 PyInstaller 把 `core/` 打成 `c2b-api` 本地 Sidecar，再由 Tauri 打包为
`src-tauri/target/release/bundle/macos/C2B Desktop.app`，并生成可复制给测试者的
`dist/C2B-Desktop-v0.3.0-macos-<架构>.zip` 与 SHA-256。当前构建机产生的是 `macos-arm64`；Intel Mac
需要在 Intel macOS 上单独构建，或由后续发布流水线生成签名的 Universal 包。

## 架构与边界

```text
C2B Desktop.app
├── Tauri：窗口、原生目录选择、应用生命周期
├── Vue 工作台：总览、图谱、规则、流程、导出
└── Python Sidecar：FastAPI + C2B Core + SQLite
    └── 仅 127.0.0.1，所有本地数据在 Application Support
```

Desktop 与 Web、CLI、Agent Skills 共用扫描、图谱、语义和文档内核。它不替代团队 Web 部署：多人协作、远程访问、
统一 PostgreSQL 和反向代理认证仍应使用 Web 工作台。

## 当前已知边界

- 当前目标为 macOS；Windows/Linux 打包尚未作为发布承诺；
- 当前不提供登录、用户角色或项目隔离，适用于单人本机；
- LLM Key 当前保存为应用数据目录下的受限文件，Keychain 集成未完成；
- 应用签名、公证、自动更新和跨架构 Universal 包是发布工程的下一步，不影响本地开发与未签名 `.app` 验证。
