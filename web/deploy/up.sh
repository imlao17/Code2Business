#!/usr/bin/env bash
# Code2Business 一键启动（形态①网页端，Docker 全套）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_SRC_DIR="$SCRIPT_DIR/../frontend"
FRONTEND_BUILD_DIR="$FRONTEND_SRC_DIR/dist"
FRONTEND_DIST_DIR="$SCRIPT_DIR/frontend-dist"
WHEEL_DIST_DIR="$SCRIPT_DIR/dist"

cd "$SCRIPT_DIR"

echo "[1/4] 构建前端 ..."
if ! command -v pnpm >/dev/null 2>&1; then
  echo "前端构建需要 pnpm（建议通过 corepack 启用）" >&2
  exit 1
fi
(
  cd "$FRONTEND_SRC_DIR"
  pnpm install --frozen-lockfile
  pnpm build
)
rm -rf "$FRONTEND_DIST_DIR"
mkdir -p "$FRONTEND_DIST_DIR"
cp -R "$FRONTEND_BUILD_DIR"/. "$FRONTEND_DIST_DIR"/
if [ ! -f "$FRONTEND_DIST_DIR/index.html" ]; then
  echo "前端构建失败：未生成 frontend-dist/index.html" >&2
  exit 1
fi

echo "[2/4] 构建 API wheel ..."
"$SCRIPT_DIR/../../cli/build-wheel.sh" --copy-to "$WHEEL_DIST_DIR"
if ! find "$WHEEL_DIST_DIR" -maxdepth 1 -type f -name 'c2b-*.whl' | grep -q .; then
  echo "wheel 准备失败：deploy/dist 下未找到 c2b wheel" >&2
  exit 1
fi

echo "[3/4] 构建后端镜像 ..."
docker compose build api

echo "[4/4] 启动 PG + API + 前端 ..."
docker compose up -d

echo ""
echo "✅ 已启动：http://localhost:8080"
echo "   模型设置：打开网页后右上角「LLM 设置」选择 OpenAI Compatible 或 Anthropic Native 并配置"
echo "   停止：./down.sh"
