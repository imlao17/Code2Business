# C2B CLI（产品②）

完整功能的命令行产品：扫描、AI 标注、模块规格、文档和问答。它面向服务器、批处理、CI 和脚本化场景，分发物为一个 Python wheel。

**它解决什么问题？** 当你不需要浏览器工作台，而需要可重复执行、可纳入自动化、可在内网服务器运行的遗留系统分析能力时，使用 CLI。

CLI 可独立安装和运行，**不依赖 Web 工作台或 Agent Skills**。默认使用本地 SQLite；生产或需要与 Web 共享结果时，再把 `C2B_DB_URL` 指向 PostgreSQL。三种产品的选择方式见根目录 [README](../README.md)。

## 适用场景

- 在服务器或开发机定期扫描多个代码仓库；
- 在 CI、脚本或批处理任务中生成系统盘点与交付文档；
- 先用确定性扫描产出代码图谱，再按需调用 LLM 做语义分析；
- 不希望部署 Docker/浏览器界面，但希望保留完整 C2B 能力。

## 安装

```bash
# 构建（产物会同步到 cli/dist/ 和 skill/vendor/）
./build-wheel.sh

# 安装（任何机器，仅需 Python 3.11+）
pip install dist/c2b-*.whl

# 国内网络慢可加镜像：
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple dist/*.whl
```

## 配置（一次性）

```bash
mkdir -p ~/.c2b
cat > ~/.c2b/config.json <<'EOF'
{ "llm_base_url": "https://api.deepseek.com/v1",
  "llm_api_key": "sk-你的key",
  "llm_model": "deepseek-chat", "llm_mode": "api" }
EOF

c2b init    # 初始化数据库（默认 SQLite；生产推荐 PostgreSQL：export C2B_DB_URL=...）
```

## 命令手册

```bash
c2b scan <代码目录> --name <项目名>       # 扫描：代码 → 代码图谱（输出 project_id）
c2b scan <代码目录> --name <项目名> --probe-only
      # 仅做规模探测；输出 project_id/scan_id 供后续 `--project-id` 复用，不代表扫描完成
c2b status <project_id>                   # 流程状态（✅🔄⬜ 六步）
c2b label <project_id> --limit 100        # AI 标注：域/对象/规则/流程/推演
      # 可选：--concurrency 16（并发）、--model deepseek-chat（快）、--no-cache
c2b module-specs <project_id>             # 全部模块需求规格
      # 可选：--name Sms01206（按名）、--module <id>、--force（绕过缓存）
c2b docs <project_id> --type overview     # 交付文档（md/word）
      # 类型：overview/functions/rules/dictionary/apis/acceptance
c2b export-specs <project_id>             # 模块规格打包 zip
c2b qa <project_id> "采购订单审批通过后会发生什么？"   # AI 问答
```

## 典型工作流：从代码到交付物

```bash
c2b scan /path/to/code --name 项目A
# → project_id: 1

c2b label 1 --limit 400          # 全量标注（约 15-25 分钟）
c2b module-specs 1               # 全部模块规格（130 个约 30-60 分钟，可 --name 单模块试点）
c2b docs 1 --type overview --output 现状说明书.md
c2b docs 1 --type rules    --output 业务规则清单.md
c2b export-specs 1 --output module-specs.zip
c2b qa 1 "系统的业务主线是什么？"
```

若只需要快速盘点规模而不想立即扫描全部代码：

```bash
c2b scan /path/to/code --name 项目A --probe-only
# 记下输出的 project_id 后，确认继续：
c2b scan /path/to/code --name 项目A --project-id <project_id>
```

## 哪些功能需要 LLM？

| 能力 | 是否需要 LLM | 说明 |
|---|---|---|
| Probe、代码扫描、模块/方法/API/SQL/表图谱 | 否 | 可离线完成；默认写入 SQLite 或指定数据库 |
| 业务域、对象、规则、流程、主线 | 是 | 执行 `c2b label` 时调用 OpenAI 兼容 LLM |
| 模块需求规格、AI 问答 | 是 | 使用相同 LLM 配置，结果可缓存 |
| Markdown / Word / Excel / ZIP 导出 | 否 | 依赖已有扫描或业务知识数据 |

## 说明

- `./build-wheel.sh` 默认同步 CLI 和 Agent Skill 的 wheel，避免多个分发形态版本不一致
- `./build-wheel.sh --copy-to <dir>` 可把同一 wheel 复制到额外目录；网页端部署脚本用它把 wheel 同步到 `web/deploy/dist/`
- **缓存**：LLM 结果缓存于 `~/.c2b/llm_cache`，重复跑不重复计费；`--no-cache`/`--force` 强制重新生成
- **成本**：DeepSeek 下全量标注 400 方法约 ¥1-2
- **与网页互通（可选）**：`C2B_DB_URL` 指向同一 PostgreSQL 数据库时，CLI 分析结果可在网页工作台中直接查看
- **探测与正式扫描**：若先执行 `c2b scan ... --probe-only`，确认后用 `c2b scan <代码目录> --name <项目名> --project-id <id>` 继续正式扫描，避免重复创建项目
- 更多使用细节见根目录 `docs/architecture.md`

## 目录

```
cli/
├── build-wheel.sh    # 一键构建 wheel
└── dist/             # 最新 CLI wheel 分发物（脚本生成）
```
