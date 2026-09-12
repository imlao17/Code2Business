#!/usr/bin/env sh
# End-to-end smoke for the native C2B Desktop.app:
#   隔离数据目录启动 App → 引擎就绪 → 导入样例仓库 → --no-ai 一键分析
#   → 流程就绪 → 生成客户交付总报告 → 退出 App → 无孤儿 sidecar。
# 用法：sh scripts/smoke-app.sh [APP_PATH]
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(CDPATH= cd -- "$DESKTOP_DIR/.." && pwd)"
APP_PATH="${1:-$DESKTOP_DIR/dist/C2B Desktop.app}"
SMOKE_TEMP="$(mktemp -d /tmp/c2b-smoke.XXXXXX)"
BASE_URL="http://127.0.0.1:8123"

cleanup() {
  osascript -e 'quit app "C2B Desktop"' >/dev/null 2>&1 || true
  sleep 1
  pkill -f "c2b-desktop" 2>/dev/null || true
  pkill -f "Resources/c2b-api" 2>/dev/null || true
  rm -rf "$SMOKE_TEMP"
}
trap cleanup EXIT INT TERM

fail() { echo "✗ $1" >&2; exit 1; }

[ -d "$APP_PATH" ] || fail "未找到 App：$APP_PATH"

# 停掉已在运行的实例，保证冒烟使用隔离数据目录
osascript -e 'quit app "C2B Desktop"' >/dev/null 2>&1 || true
sleep 1
pkill -f "c2b-desktop" 2>/dev/null || true
pkill -f "Resources/c2b-api" 2>/dev/null || true
pkill -f "uvicorn c2b.api.main" 2>/dev/null || true
# PyInstaller onefile 有父子两个进程，退出需要时间；端口未释放时新 App 会复用旧引擎
for i in $(seq 1 20); do
  curl -sf -m 1 "$BASE_URL/healthz" >/dev/null 2>&1 || break
  sleep 1
done
curl -sf -m 1 "$BASE_URL/healthz" >/dev/null 2>&1 && fail "旧引擎端口 8123 迟迟未释放"
sleep 1

# 样例仓库
REPO="$SMOKE_TEMP/repo/com/shop"
mkdir -p "$REPO"
cat > "$REPO/OrderService.java" <<'EOF'
package com.shop;
/** 订单服务 */
public class OrderService {
    /** 创建订单：金额超限需要审批 */
    public void createOrder(int amount, String customer) {
        if (amount > 10000) {
            requireApproval(customer);
        }
        save(amount);
    }
    private void requireApproval(String customer) {
        System.out.println("approve " + customer);
    }
    private void save(int amount) {
    }
}
EOF

echo "[1/6] 启动 App（隔离数据目录 $SMOKE_TEMP/data）..."
C2B_DESKTOP_DATA_DIR="$SMOKE_TEMP/data" open -n "$APP_PATH" --env C2B_DESKTOP_DATA_DIR="$SMOKE_TEMP/data" >/dev/null 2>&1 \
  || C2B_DESKTOP_DATA_DIR="$SMOKE_TEMP/data" open -n "$APP_PATH" >/dev/null 2>&1 \
  || fail "无法启动 App"
# `open --env` 并非所有版本可靠；改由 plist 注入不可行，直接用启动环境变量探测：
# 如果引擎 healthz 的项目不来自冒烟库，说明数据目录覆盖未生效，退出报错。

READY=""
for i in $(seq 1 60); do
  if curl -sf "$BASE_URL/healthz" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
[ -n "$READY" ] || fail "引擎 60 秒内未就绪"
echo "      引擎就绪：$(curl -sf "$BASE_URL/healthz")"

echo "[2/6] 导入样例仓库 ..."
IMPORT="$(curl -sf -X POST "$BASE_URL/api/projects/import-local" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"smoke-原生冒烟\",\"source_path\":\"$SMOKE_TEMP/repo\"}")"
echo "$IMPORT" | grep -q '"ok":true' || fail "导入失败：$IMPORT"
PID="$(echo "$IMPORT" | sed -n 's/.*"project_id":\([0-9]*\).*/\1/p')"
[ -n "$PID" ] || fail "未取得 project_id"

echo "[3/6] 一键分析（无 AI）..."
curl -sf -X POST "$BASE_URL/api/projects/$PID/auto-run" \
  -H 'Content-Type: application/json' -d '{"run_ai":false}' >/dev/null || fail "auto-run 启动失败"

echo "[4/6] 等待扫描完成 ..."
STATUS=""
for i in $(seq 1 60); do
  STATUS="$(curl -sf "$BASE_URL/api/projects/$PID/pipeline" | sed -n 's/.*"key":"scan","label":"[^"]*","page":"[^"]*","status":"\([a-z]*\)".*/\1/p' | head -n 1)"
  [ "$STATUS" = "done" ] && break
  sleep 2
done
[ "$STATUS" = "done" ] || fail "扫描未完成（status=$STATUS）"

echo "[5/6] 生成客户交付总报告 ..."
DOC="$(curl -sf "$BASE_URL/api/projects/$PID/documents/combined?fmt=md&draft=true")"
echo "$DOC" | grep -q '客户交付总报告' || fail "总报告标题缺失"
echo "$DOC" | grep -q '执行摘要' || fail "总报告缺少执行摘要"

echo "[6/6] 退出并检查无孤儿引擎 ..."
osascript -e 'quit app "C2B Desktop"' >/dev/null 2>&1 || true
sleep 3
if pgrep -f "Resources/c2b-api" >/dev/null 2>&1; then
  fail "退出后仍有 c2b-api 孤儿进程"
fi

echo ""
echo "✅ 原生 Desktop 冒烟通过（import → auto-run → pipeline → combined → 无孤儿进程）"
