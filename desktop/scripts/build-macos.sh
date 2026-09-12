#!/usr/bin/env sh
# Build the fully native SwiftUI C2B Desktop.app with the Python sidecar.
#
# 壳：Swift 编译（desktop/app，无 WebView）；引擎：PyInstaller 打包的 core。
# 接口与旧 Tauri 流程兼容：设 C2B_MACOS_SIGNING_IDENTITY 时用其签名，否则 ad-hoc。
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)"
APP_DIR="$DESKTOP_DIR/app"
PYTHON_BIN="${C2B_DESKTOP_PYTHON:-$REPO_DIR/.venv/bin/python}"
RUNTIME_DIR="$DESKTOP_DIR/runtime"
CORE_SRC_DIR="$REPO_DIR/core/src"
SIDECAR_PATH="$RUNTIME_DIR/c2b-api"
DIST_DIR="$DESKTOP_DIR/dist"
APP_PATH="$DIST_DIR/C2B Desktop.app"
ARCH="$(uname -m)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_DIR/core/pyproject.toml" | head -n 1)"
SIGNING_IDENTITY="${C2B_MACOS_SIGNING_IDENTITY:-}"
PLACEHOLDER_COPY="$(mktemp)"
BUILD_TEMP="$(mktemp -d)"

cleanup() {
  if [ -f "$PLACEHOLDER_COPY" ]; then
    cp "$PLACEHOLDER_COPY" "$SIDECAR_PATH"
    chmod 755 "$SIDECAR_PATH"
    rm -f "$PLACEHOLDER_COPY"
  fi
  if [ -d "$BUILD_TEMP" ]; then
    rm -rf "$BUILD_TEMP"
  fi
}
trap cleanup EXIT INT TERM

if [ ! -x "$PYTHON_BIN" ]; then
  echo "未找到 Python 运行时：$PYTHON_BIN" >&2
  echo "可通过 C2B_DESKTOP_PYTHON 指定已安装 core 依赖的 Python。" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "macOS 打包需要 PyInstaller。请执行：$PYTHON_BIN -m pip install pyinstaller" >&2
  exit 1
fi
if ! command -v swift >/dev/null 2>&1; then
  echo "未找到 Swift 工具链（需要 Xcode 或 Command Line Tools）。" >&2
  exit 1
fi
if [ -z "$VERSION" ]; then
  echo "无法从 core/pyproject.toml 读取版本号" >&2
  exit 1
fi

RESOLVED_CORE="$(PYTHONPATH="$CORE_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -c 'import c2b; print(c2b.__file__)')"
case "$RESOLVED_CORE" in
  "$CORE_SRC_DIR"/*) ;;
  *)
    echo "Desktop 打包未解析到当前仓库 Core：$RESOLVED_CORE" >&2
    exit 1
    ;;
esac

cp "$SIDECAR_PATH" "$PLACEHOLDER_COPY"

echo "[1/4] 构建本地 Python 分析引擎 ..."
rm -rf "$RUNTIME_DIR/build" "$RUNTIME_DIR/work"
PYTHONPATH="$CORE_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -m PyInstaller --noconfirm --clean --onefile \
  --name c2b-api \
  --paths "$CORE_SRC_DIR" \
  --distpath "$RUNTIME_DIR/build" \
  --workpath "$RUNTIME_DIR/work" \
  --specpath "$RUNTIME_DIR/work" \
  "$RUNTIME_DIR/c2b_api.py"
cp "$RUNTIME_DIR/build/c2b-api" "$SIDECAR_PATH"
chmod 755 "$SIDECAR_PATH"

echo "[2/4] 编译原生 SwiftUI 壳 ..."
swift build -c release --package-path "$APP_DIR"

echo "[3/4] 组装 C2B Desktop.app ..."
rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"
cp "$APP_DIR/.build/release/C2BDesktop" "$APP_PATH/Contents/MacOS/c2b-desktop"
cp "$SIDECAR_PATH" "$APP_PATH/Contents/Resources/c2b-api"
chmod 755 "$APP_PATH/Contents/Resources/c2b-api"
cp "$APP_DIR/assets/AppIcon.icns" "$APP_PATH/Contents/Resources/AppIcon.icns"
cat > "$APP_PATH/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>C2B Desktop</string>
    <key>CFBundleDisplayName</key><string>C2B Desktop</string>
    <key>CFBundleExecutable</key><string>c2b-desktop</string>
    <key>CFBundleIdentifier</key><string>com.lao17.code2business</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
</dict>
</plist>
PLIST
if [ -n "$SIGNING_IDENTITY" ]; then
  codesign --force --deep --sign "$SIGNING_IDENTITY" "$APP_PATH"
  codesign --verify --strict "$APP_PATH"
else
  codesign --force --deep --sign - "$APP_PATH"
fi

echo "[4/4] 生成分发压缩包 ..."
mkdir -p "$DIST_DIR"
ARCHIVE_PATH="$DIST_DIR/C2B-Desktop-v$VERSION-macos-$ARCH.zip"
DMG_PATH="$DIST_DIR/C2B-Desktop-v$VERSION-macos-$ARCH.dmg"
rm -f "$ARCHIVE_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE_PATH"
DMG_STAGE="$BUILD_TEMP/dmg"
mkdir -p "$DMG_STAGE"
cp -R "$APP_PATH" "$DMG_STAGE/C2B Desktop.app"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create -quiet -volname "C2B Desktop" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"

echo ""
echo "完成：$APP_PATH"
echo "ZIP：$ARCHIVE_PATH"
echo "DMG：$DMG_PATH"
for artifact in "$ARCHIVE_PATH" "$DMG_PATH"; do
  (cd "$DIST_DIR" && shasum -a 256 "$(basename "$artifact")" > "$(basename "$artifact").sha256")
  cat "$artifact.sha256"
done
