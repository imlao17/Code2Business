---
name: c2b-analyze
description: Code2Business 遗留系统业务知识还原（完整版）主入口。当用户要求"分析这个代码库""还原业务""生成需求规格""重构前梳理""系统盘点"，或给出代码目录/Git 地址要求产出业务文档时使用。本技能编排完整流程：扫描（c2b-scan）→ AI 语义标注（c2b-semantic）→ 交付文档（c2b-docs），产出与 Code2Business 网页端相同的成果（模块图谱/业务对象/规则卡片/模块需求规格/交付文档）。依赖 c2b Python 包与 DeepSeek API。触发词：代码分析、业务还原、需求规格、系统盘点、重构前、逆向、遗留系统。
---

# Code2Business 完整流程（主入口）

把旧代码库还原为业务资产，产出与网页端相同的结果。**必须按以下三个阶段顺序执行**，每阶段完成后向用户汇报进度。

## 前置检查

1. 确认 c2b 已安装：`c2b --help`（若失败：
   - 若本机由 install.sh 安装过：先 `export PATH="$HOME/.c2b/venv/bin:$PATH"`
   - 否则见 skill/README.md 或执行 skill/install.sh）
2. 确认 LLM 配置：`ls ~/.c2b/config.json` 且含 api_key（无则提示用户配置）
3. 确认数据库可用：`c2b init`（与网页共用同一 C2B_DB_URL）

## 阶段一：扫描（调用 c2b-scan）

```bash
c2b scan <代码根目录或仓库路径> --name <项目名>
```

- 输出规模报告（行数/模块/编码/历史版本）并展示给用户
- 记录返回的 project_id，后续所有命令依赖它
- 若用户先要求秒级预探测，执行 `c2b scan <目录> --name <项目名> --probe-only`，记录返回的 `project_id`
- 用户确认继续后，执行 `c2b scan <目录> --name <项目名> --project-id <project_id>` 进入正式扫描，避免重复创建项目

## 阶段二：语义标注（调用 c2b-semantic）

```bash
c2b label <project_id> --limit 100        # 域/对象/规则/流程/推演（可调大 limit）
c2b module-specs <project_id>             # 全部模块需求规格（130 个约 1 小时，可 --name 指定）
```

- 展示标注统计（规则数/对象数/流程数/推演）
- 询问用户是否要全量标注（--limit 400+）或模块规格全部分析

## 阶段三：交付（调用 c2b-docs）

```bash
c2b docs <project_id> --type overview --output 系统现状说明书.md
c2b docs <project_id> --type rules --output 业务规则清单.md
c2b export-specs <project_id> --output module-specs.zip
```

## 完成汇报模板

```markdown
## 分析完成报告
- 代码规模：X 行 / Y 文件 / N 模块（档位：XL）
- 业务图谱：M 条规则 / O 个业务对象 / F 条流程 / 推演主线
- 交付物：
  - 系统现状说明书.md
  - 模块需求规格.zip（K 份）
- 待确认项：Q 项（见 questions 表，网页「待确认项」页可处理）
- 提示：若已启动 Web 部署，可在 http://localhost:8080 查看全部结果（需指向同一数据库）
```

## 质量约束

- 每个阶段输出后向用户展示关键数据，不要静默执行
- LLM 结果不可信时（规则数异常少/空），提示用户检查 LLM Key 或重跑
- 涉及大量 LLM 调用的操作（全量标注/全部模块规格）先询问用户
