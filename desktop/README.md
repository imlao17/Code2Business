# C2B Desktop（macOS 本地工作台）

> 当前正式版本：[v0.3.0](https://github.com/imlao17/Code2Business/releases/tag/v0.3.0)。C2B Desktop 是 Web 工作台的本地优先分发形态，不是第四套业务引擎。Release 附带的 macOS 包为 Apple Silicon（arm64）未签名试用包；签名、公证与架构边界见下文。

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
- 非敏感 LLM 设置保存在 C2B 应用数据目录；API Key 保存到当前用户的 macOS 钥匙串，不写入 JSON 配置文件。
  从 v0.3.0 早期本地包升级时，下一次保存 LLM 设置会把已有明文 Key 迁移进钥匙串；仍不应把多人共用的
  macOS 账户当作安全隔离边界。

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
`dist/C2B-Desktop-v0.3.0-macos-<架构>.zip`、带 Applications 快捷方式的 `.dmg` 以及两者的
`.sha256`。当前构建机产生的是 `macos-arm64`；Intel Mac
需要在 Intel macOS 上单独构建，或由后续发布流水线生成签名的 Universal 包。

应用图标源文件发生变化时单独执行 `pnpm icon` 并提交生成结果；常规构建不会改写仓库中的图标资产。
脚本会强制校验 PyInstaller 解析的是当前仓库 `core/src`，避免构建机中曾安装的旧版 wheel 被误打入 Desktop。

`pnpm build` 生成的是本地/内部试用包。面向其他用户正式分发时，必须使用 Developer ID Application 证书和
Apple 公证：

```bash
export C2B_MACOS_SIGNING_IDENTITY="Developer ID Application: 组织名称 (TEAMID)"
export C2B_NOTARY_KEYCHAIN_PROFILE="c2b-notary"
pnpm release:macos
```

该命令会生成已签名、公证、通过 Gatekeeper 复核的 ZIP、DMG 及各自 SHA-256；凭据准备和完整发布清单见
[macOS 发布说明](RELEASING.md)。它会拒绝用 `Apple Development` 证书冒充正式分发签名。

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
- 正式签名、公证与 DMG 流程已经接入，但产出正式包仍要求发布者提供有效的 Developer ID Application 证书和
  Apple 公证凭据；仓库和本地开发包不携带这些秘密；
- 自动更新和跨架构 Universal 包尚未实现；当前包名明确包含 `arm64` 或 `x86_64`，避免误导用户。
