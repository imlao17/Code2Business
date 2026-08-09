#!/usr/bin/env bash
# 构建可分发镜像并保存为离线包（形态①网页端独立分发）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_SRC_DIR="$SCRIPT_DIR/../frontend"
FRONTEND_BUILD_DIR="$FRONTEND_SRC_DIR/dist"
FRONTEND_DIST_DIR="$SCRIPT_DIR/frontend-dist"
WHEEL_DIST_DIR="$SCRIPT_DIR/dist"
IMAGE_ARCHIVE="$SCRIPT_DIR/c2b-web-images.tar.gz"
BUNDLE_NAME="c2b-web-deploy"
BUNDLE_ARCHIVE="$SCRIPT_DIR/${BUNDLE_NAME}.tar.gz"
RUNTIME_IMAGES=(
  "c2b-api:latest"
  "postgres:16-alpine"
  "nginx:alpine"
)

checksum_cmd() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "首次构建需要 python3，以生成数据库密码配置" >&2
    exit 1
  fi
  generated_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  (
    umask 077
    sed "s/replace-with-a-long-url-safe-secret/$generated_password/" \
      .env.example > .env
  )
  echo "已创建受限权限的 .env，并生成 PostgreSQL 密码。请妥善备份该文件。"
fi

echo "[1/5] 构建前端 ..."
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

echo "[2/5] 构建 API wheel ..."
"$SCRIPT_DIR/../../cli/build-wheel.sh" --copy-to "$WHEEL_DIST_DIR"
if ! find "$WHEEL_DIST_DIR" -maxdepth 1 -type f -name 'c2b-*.whl' | grep -q .; then
  echo "wheel 准备失败：deploy/dist 下未找到 c2b wheel" >&2
  exit 1
fi

echo "[3/5] 构建 API 镜像 ..."
docker compose build api
if ! docker image inspect c2b-api:latest >/dev/null 2>&1; then
  echo "镜像构建失败：未找到 c2b-api:latest" >&2
  exit 1
fi

for image in "${RUNTIME_IMAGES[@]:1}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "拉取运行时镜像 $image ..."
    docker pull "$image"
  fi
done

echo "[4/5] 保存镜像为离线包 ..."
rm -f "$IMAGE_ARCHIVE"
docker save "${RUNTIME_IMAGES[@]}" | gzip > "$IMAGE_ARCHIVE"
if [ ! -s "$IMAGE_ARCHIVE" ]; then
  echo "镜像导出失败：$IMAGE_ARCHIVE 为空" >&2
  exit 1
fi

echo "[5/5] 生成离线部署包 ..."
tmp_dir="$(mktemp -d)"
bundle_dir="$tmp_dir/$BUNDLE_NAME"
mkdir -p "$bundle_dir"
cp docker-compose.yml Dockerfile .env.example README.md "$IMAGE_ARCHIVE" "$bundle_dir/"
cp -R nginx "$bundle_dir/"
cp -R "$FRONTEND_DIST_DIR" "$bundle_dir/frontend-dist"
cp -R "$WHEEL_DIST_DIR" "$bundle_dir/dist"

(
  cd "$bundle_dir"
  find . -type f ! -name 'MANIFEST.sha256' | sort | while read -r file; do
    checksum_cmd "$file"
  done > MANIFEST.sha256
)

tar czf "$BUNDLE_ARCHIVE" -C "$tmp_dir" "$BUNDLE_NAME"
rm -rf "$tmp_dir"

echo ""
echo "✅ 离线部署包："
ls -lh "$BUNDLE_ARCHIVE" "$IMAGE_ARCHIVE" | awk '{print "  ", $NF, $5}'
echo ""
echo "目标机器部署："
echo "  tar xzf $(basename "$BUNDLE_ARCHIVE")"
echo "  cd $BUNDLE_NAME"
echo "  cp .env.example .env && chmod 600 .env  # 修改部署密码与安全配置"
echo "  shasum -a 256 -c MANIFEST.sha256    # Linux 可改用 sha256sum -c"
echo "  docker load < c2b-web-images.tar.gz"
echo "  docker compose up -d"
echo "  访问 http://localhost:8080"
