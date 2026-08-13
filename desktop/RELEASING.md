# C2B Desktop macOS 发布说明

本说明用于生成可交给普通 macOS 用户安装的正式包。日常本地开发和内部试用不需要这些凭据，继续执行
`pnpm build` 即可。

## 发布产物的可信边界

正式发布命令只接受 `Developer ID Application` 身份。`Apple Development` 证书只适合开发调试，不能替代
面向 App Store 之外用户的 Developer ID 签名，也不能让应用通过 Apple 公证。

发布脚本会依次完成：

1. 构建 Python Sidecar 和 Tauri `.app`；
2. 使用 Developer ID Application 签名并严格校验应用包；
3. 将应用提交 Apple 公证，等待结果并装订公证凭据；
4. 生成带“拖到 Applications”入口的 ZIP 与 DMG；
5. 签名、公证并装订 DMG；
6. 使用 Gatekeeper 复核应用和 DMG，再生成 SHA-256 文件。

任何一步失败都会停止，不会把未公证产物报告成正式分发包。

## 一次性准备

需要有效的 Apple Developer Program 会员资格，并在构建机钥匙串安装
`Developer ID Application: ...` 证书及其私钥。可以用以下命令确认：

```bash
security find-identity -v -p codesigning
```

然后将 Apple 公证凭据存入当前用户钥匙串。`c2b-notary` 是本地配置名称，不是密码：

```bash
xcrun notarytool store-credentials c2b-notary \
  --apple-id "你的 Apple ID" \
  --team-id "你的 Team ID" \
  --password "App 专用密码"
```

密钥和密码只保存在 macOS 钥匙串中，不写入仓库、脚本或环境示例文件。

## 构建正式包

```bash
export C2B_MACOS_SIGNING_IDENTITY="Developer ID Application: 组织名称 (TEAMID)"
export C2B_NOTARY_KEYCHAIN_PROFILE="c2b-notary"
cd desktop
pnpm release:macos
```

成功后在 `desktop/dist/` 中得到：

- `C2B-Desktop-v<版本>-macos-<架构>.zip`
- `C2B-Desktop-v<版本>-macos-<架构>.zip.sha256`
- `C2B-Desktop-v<版本>-macos-<架构>.dmg`
- `C2B-Desktop-v<版本>-macos-<架构>.dmg.sha256`

文件名版本由 `desktop/package.json` 自动读取，避免打包脚本和产品版本发生漂移。

## 发布前检查

- `desktop/package.json`、`desktop/src-tauri/tauri.conf.json`、`core/pyproject.toml`、
  `core/src/c2b/__init__.py` 和各产品 README 的版本一致；
- `CHANGELOG.md` 已把当前版本从“未发布”调整为正式发布日期；
- 后端测试、Web 生产构建、Desktop `cargo check` 和文档链接检查全部通过；
- 在一台没有开发环境的 Mac 上安装 DMG，验证启动、选择本地仓库、扫描、模型配置和退出；
- GitHub Release 上传 ZIP、DMG 和两个 `.sha256`，不要上传本地数据库、配置或项目源码。

## 当前架构范围

脚本在当前构建机架构上生成原生包。Apple Silicon 构建机得到 `arm64`，Intel 构建机得到 `x86_64`。
Universal 包需要分别生成并合并 Rust 应用和 PyInstaller Sidecar，当前流程会明确标注架构，不把单架构包伪装成
Universal 包。
