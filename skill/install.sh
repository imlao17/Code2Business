#!/usr/bin/env bash
# Code2Business 技能组独立安装（形态③）
# 本目录自带 c2b 应用 wheel；第三方 Python 依赖由 pip 从配置的镜像安装。
# 用法：./install.sh [目标skills目录]   （默认 ~/.agents/skills）
set -euo pipefail
cd "$(dirname "$0")"

TARGET="${1:-$HOME/.agents/skills}"
C2B_HOME="$HOME/.c2b"

echo "== 1/4 安装 c2b 运行时（本地 vendor wheel）=="
mkdir -p "$C2B_HOME"
if [ ! -x "$C2B_HOME/venv/bin/c2b" ]; then
  python3 -m venv "$C2B_HOME/venv"
fi
"$C2B_HOME/venv/bin/pip" install -q --upgrade pip
# 依赖从 PyPI 下载；国内网络慢时可指定镜像，如：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./install.sh
PIP_ARGS=(-q --timeout 60 --retries 3)
if [ -n "${PIP_INDEX_URL:-}" ]; then
  PIP_ARGS+=(-i "$PIP_INDEX_URL")
fi
"$C2B_HOME/venv/bin/pip" install "${PIP_ARGS[@]}" vendor/*.whl
echo "  ✓ c2b 已安装到 $C2B_HOME/venv"

echo "== 2/4 复制 skills =="
mkdir -p "$TARGET"
for skill in c2b-analyze c2b-scan c2b-semantic c2b-docs code2business; do
  rm -rf "$TARGET/$skill"
  cp -r "$skill" "$TARGET/"
  echo "  ✓ $skill"
done

echo "== 3/4 LLM 配置检查 =="
if [ -f "$C2B_HOME/config.json" ]; then
  echo "  ✓ 已存在（$C2B_HOME/config.json）"
else
  cat > "$C2B_HOME/config.json" <<'EOF'
{
  "llm_base_url": "https://api.deepseek.com/v1",
  "llm_api_key": "",
  "llm_model": "deepseek-chat",
  "llm_mode": "api"
}
EOF
  echo "  ⚠️  已创建模板，请编辑 $C2B_HOME/config.json 填入 llm_api_key"
fi

echo "== 4/4 PATH 提示 =="
echo "  将以下一行加入 ~/.zshrc（或 ~/.bashrc）后重新打开终端："
echo "  export PATH=\"$C2B_HOME/venv/bin:\$PATH\""

echo ""
echo "✅ 安装完成。使用：对 Agent 说「用 c2b 分析 <代码目录>」"
echo "   或直接命令行：c2b scan / label / docs ..."
