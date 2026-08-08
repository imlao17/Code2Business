#!/usr/bin/env bash
# 构建可分发 wheel 包（形态② CLI 的分发物）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INVOKE_DIR="$PWD"
CORE_DIR="$SCRIPT_DIR/../core"
CORE_DIST="$CORE_DIR/dist"
CLI_DIST="$SCRIPT_DIR/dist"
SKILL_VENDOR="$SCRIPT_DIR/../skill/vendor"
COPY_TARGETS=("$CLI_DIST" "$SKILL_VENDOR")

resolve_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$INVOKE_DIR/$1" ;;
  esac
}

usage() {
  cat <<'EOF'
用法：
  ./build-wheel.sh [--copy-to <dir>]

说明：
  - 默认把最新 wheel 同步到 cli/dist/ 和 skill/vendor/
  - 可重复传入 --copy-to，把同一 wheel 复制到额外目录
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --copy-to)
      if [ $# -lt 2 ]; then
        echo "缺少 --copy-to 的目录参数" >&2
        exit 1
      fi
      COPY_TARGETS+=("$(resolve_path "$2")")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

cd "$CORE_DIR"

if [ ! -d .venv ] && command -v python3 >/dev/null; then
  echo "创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi

PY=python3
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
fi

echo "安装构建工具 ..."
"$PY" -m pip install -q build

mkdir -p "$CORE_DIST"
find "$CORE_DIST" -maxdepth 1 -type f -name 'c2b-*.whl' -delete

echo "构建 wheel ..."
"$PY" -m build --wheel --outdir "$CORE_DIST" >/dev/null

WHEEL_PATH="$(find "$CORE_DIST" -maxdepth 1 -type f -name 'c2b-*.whl' | head -n 1)"
if [ -z "$WHEEL_PATH" ]; then
  echo "构建失败：未生成 c2b wheel" >&2
  exit 1
fi

for target in "${COPY_TARGETS[@]}"; do
  mkdir -p "$target"
  find "$target" -maxdepth 1 -type f -name 'c2b-*.whl' -delete
  cp "$WHEEL_PATH" "$target/"
done

ls -lh "$WHEEL_PATH"
echo "✅ CLI 分发：pip install $CLI_DIST/$(basename "$WHEEL_PATH")"
