#!/usr/bin/env sh
# Build a macOS .app that carries the same Python Core as Web/CLI.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)"
PYTHON_BIN="${C2B_DESKTOP_PYTHON:-$REPO_DIR/.venv/bin/python}"
RUNTIME_DIR="$DESKTOP_DIR/runtime"
CORE_SRC_DIR="$REPO_DIR/core/src"
SIDECAR_PATH="$RUNTIME_DIR/c2b-api"
DIST_DIR="$DESKTOP_DIR/dist"
APP_PATH="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/C2B Desktop.app"
ARCH="$(uname -m)"
VERSION="$(node -p "require('$DESKTOP_DIR/package.json').version")"
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
RESOLVED_CORE="$(PYTHONPATH="$CORE_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -c 'import c2b; print(c2b.__file__)')"
case "$RESOLVED_CORE" in
  "$CORE_SRC_DIR"/*) ;;
  *)
    echo "Desktop 打包未解析到当前仓库 Core：$RESOLVED_CORE" >&2
    exit 1
    ;;
esac
if [ -n "$SIGNING_IDENTITY" ]; then
  if ! security find-identity -v -p codesigning | grep -Fq "\"$SIGNING_IDENTITY\""; then
    echo "钥匙串中没有找到签名身份：$SIGNING_IDENTITY" >&2
    exit 1
  fi
  export APPLE_SIGNING_IDENTITY="$SIGNING_IDENTITY"
fi

cp "$SIDECAR_PATH" "$PLACEHOLDER_COPY"

echo "[1/3] 构建本地 Python 分析引擎 ..."
rm -rf "$RUNTIME_DIR/build" "$RUNTIME_DIR/work"
PYTHONPATH="$CORE_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -m PyInstaller --noconfirm --clean --onefile \
  --name c2b-api \
  --paths "$REPO_DIR/core/src" \
  --distpath "$RUNTIME_DIR/build" \
  --workpath "$RUNTIME_DIR/work" \
  --specpath "$RUNTIME_DIR/work" \
  "$RUNTIME_DIR/c2b_api.py"
cp "$RUNTIME_DIR/build/c2b-api" "$SIDECAR_PATH"
chmod 755 "$SIDECAR_PATH"

echo "[2/3] 安装桌面壳依赖 ..."
pnpm --dir "$DESKTOP_DIR" install --frozen-lockfile

echo "[3/3] 构建 C2B Desktop.app ..."
pnpm --dir "$DESKTOP_DIR" exec tauri build --bundles app

if [ -n "$SIGNING_IDENTITY" ]; then
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
fi

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
