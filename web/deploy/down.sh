#!/usr/bin/env bash
# 停止并移除容器（保留数据卷 c2b_pgdata / c2b_data）
cd "$(dirname "$0")"
docker compose down
echo "✅ 已停止。数据保留在 volume 中，./up.sh 可再次启动。"
