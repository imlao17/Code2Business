#!/usr/bin/env sh
# Build a macOS .app that carries the same Python Core as Web/CLI.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)"
PYTHON_BIN="${C2B_DESKTOP_PYTHON:-$REPO_DIR/.venv/bin/python}"
RUNTIME_DIR="$DESKTOP_DIR/runtime"
SIDECAR_PATH="$RUNTIME_DIR/c2b-api"
DIST_DIR="$DESKTOP_DIR/dist"
APP_PATH="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/C2B Desktop.app"
ARCH="$(uname -m)"
PLACEHOLDER_COPY="$(mktemp)"

cleanup() {
  if [ -f "$PLACEHOLDER_COPY" ]; then
    cp "$PLACEHOLDER_COPY" "$SIDECAR_PATH"
    chmod 755 "$SIDECAR_PATH"
    rm -f "$PLACEHOLDER_COPY"
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

cp "$SIDECAR_PATH" "$PLACEHOLDER_COPY"

echo "[1/4] 构建本地 Python 分析引擎 ..."
rm -rf "$RUNTIME_DIR/build" "$RUNTIME_DIR/work"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean --onefile \
  --name c2b-api \
  --paths "$REPO_DIR/core/src" \
  --distpath "$RUNTIME_DIR/build" \
  --workpath "$RUNTIME_DIR/work" \
  --specpath "$RUNTIME_DIR/work" \
  "$RUNTIME_DIR/c2b_api.py"
cp "$RUNTIME_DIR/build/c2b-api" "$SIDECAR_PATH"
chmod 755 "$SIDECAR_PATH"

echo "[2/4] 安装桌面壳依赖 ..."
pnpm --dir "$DESKTOP_DIR" install --frozen-lockfile

echo "[3/4] 生成 macOS 应用图标 ..."
pnpm --dir "$DESKTOP_DIR" exec tauri icon "$DESKTOP_DIR/src-tauri/icons/c2b.svg"

echo "[4/4] 构建 C2B Desktop.app ..."
pnpm --dir "$DESKTOP_DIR" exec tauri build --bundles app

mkdir -p "$DIST_DIR"
ARCHIVE_PATH="$DIST_DIR/C2B-Desktop-v0.3.0-macos-$ARCH.zip"
rm -f "$ARCHIVE_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE_PATH"

echo ""
echo "完成：$APP_PATH"
echo "分发包：$ARCHIVE_PATH"
shasum -a 256 "$ARCHIVE_PATH"
