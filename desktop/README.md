# C2B Desktop（macOS 原生应用）

> 当前正式版本：[v0.6.0](https://github.com/imlao17/Code2Business/releases/tag/v0.6.0)。C2B Desktop 是 Web 工作台的本地优先分发形态，不是第四套业务引擎。Release 附带的 macOS 包为 Apple Silicon（arm64）未签名试用包；签名、公证与架构边界见下文。

C2B Desktop 是一个**完全原生的 SwiftUI 应用**：原生窗口、侧栏、菜单和目录选择器，没有 WebView。业务分析引擎仍是三产品共享的 `core/`（Python），以 PyInstaller sidecar 形式内置于应用包 `Contents/Resources/c2b-api`，只在 127.0.0.1:8123 本机回环监听。

```
C2B Desktop.app
├── Contents/MacOS/c2b-desktop        ← SwiftUI 原生壳（desktop/app Swift 编译产物）
└── Contents/Resources/c2b-api        ← Python 分析引擎（PyInstaller onefile）
```

## 它解决什么问题？

不想部署 Docker、不打开浏览器，在 Mac 上双击即用的本地工作台：选择本机代码目录 → 一键分析（扫描 → AI 归纳 → 模块分析）→ 确认规则 → 下载《客户交付总报告》。数据全部保存在本机。

## 使用

```bash
# 从 Release 下载 dmg 拖入 Applications，或从源码构建（见下文）后直接运行
open "/Applications/C2B Desktop.app"
```

核心界面：项目总览（六步流程条 + 统计卡）、流程与规则（确认/修改/废弃/批量/按可信度一键确认）、业务对象与流程、业务推演、数据与接口、待确认项、文档与交付物（客户交付总报告 + 六类专项文档）、帮助（三步指引与术语表）。LLM 设置在菜单栏「C2B Desktop → 设置」。

数据目录：`~/Library/Application Support/com.lao17.code2business/`（沿用历史 bundle id，从旧版本升级数据无需迁移）。

#### 试用包首次打开（macOS Sequoia 及以上必读）

未签名试用包从浏览器下载后，macOS 13+ 可能提示"已损坏，无法打开"。**App 并未损坏**，
这是 Gatekeeper 对无公证包的隔离拦截；Sequoia 已不提供"右键打开"绕过，请点「取消」后执行：

```bash
xattr -dr com.apple.quarantine "/Applications/C2B Desktop.app"
# 若 dmg 文件本身也被拦截，对它执行同样命令后再打开
xattr -d com.apple.quarantine ~/Downloads/C2B-Desktop-v0.6.0-macos-arm64.dmg
```

## 从源码构建

需要：Xcode 或 Command Line Tools（Swift 5.9+）、Python 3.11+ 与仓库 `.venv`（含 core 可编辑安装与 PyInstaller）。

```bash
cd desktop
sh scripts/build-macos.sh
```

脚本流程：PyInstaller 把 `core/` 打成 `runtime/c2b-api` → `swift build -c release` 编译原生壳 → 组装 `dist/C2B Desktop.app`（Info.plist 由脚本生成，版本号以 `core/pyproject.toml` 为单一来源）→ 生成 `dist/C2B-Desktop-v<版本>-macos-arm64.zip` 与带 Applications 快捷方式的 `.dmg` 及 `.sha256`。

本地开发调试壳时（引擎走仓库源码）：

```bash
cd desktop/app
C2B_DEV_REPO_ROOT=<仓库根> C2B_DESKTOP_DATA_DIR=/tmp/c2b-dev-data swift run
```

端到端冒烟（隔离数据目录，验证 启动→导入→一键分析→总报告→退出无孤儿进程）：

```bash
sh scripts/smoke-app.sh [APP_PATH]   # 默认 dist/C2B Desktop.app
```

单元测试：`swift test --package-path desktop/app`。

## 正式分发（Developer ID 签名与公证）

`pnpm build` 已移除；试用包构建用 `sh scripts/build-macos.sh`。面向其他用户正式分发时，必须使用 Developer ID Application 证书和 Apple 公证：

```bash
export C2B_MACOS_SIGNING_IDENTITY="Developer ID Application: 组织名称 (TEAMID)"
export C2B_NOTARY_KEYCHAIN_PROFILE="c2b-notary"
cd desktop
sh scripts/release-macos.sh
```

发布脚本的六步流程（构建→签名→公证→装订→DMG→Gatekeeper 复核）与凭据准备见 [RELEASING.md](RELEASING.md)。

## 架构说明

- **壳（SwiftUI）**：`desktop/app/Sources/C2BDesktop/`——`SidecarController`（引擎生命周期：环境变量注入、8123 就绪轮询、软单实例、退出清理与 `C2B_DESKTOP_PARENT_PID` 孤儿防护）、`APIClient`（loopback HTTP）、`AppState`（项目/流程/任务轮询）、各页面视图。
- **引擎（Python）**：与 Web/CLI/Skills 完全同一份 `core/`，通过 HTTP API 消费；Desktop 专属端点 `import-local` 仅在 `C2B_DESKTOP_MODE=1` 时放行。
- **不共享 Web 前端**：v0.6.0 起 Desktop 不再加载 Vue 页面；Web 产品的 Vue 前端继续独立存在。三种产品的选择方式见根目录 [README](../README.md)。
- 图标源文件在 `desktop/app/assets/`；打包脚本强制校验 PyInstaller 解析的是当前仓库 `core/src`，避免旧版 wheel 被误打入。
- 当前构建机产生 `macos-arm64`；Intel Mac 需在 Intel 机器单独构建或由后续流水线出 Universal 包。

## 已知边界（v1）

模块需求规格批量分析、AI 问答（QA）、R1-R3 运行证据录入暂未进入原生界面（可用 CLI/Web 操作同一数据库）。菜单内 LLM 设置的密钥保存在本机 `config` 目录（沿用 `settings_store`，Desktop 模式下写入 macOS 钥匙串）。
