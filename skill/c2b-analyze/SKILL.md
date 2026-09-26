---
name: c2b-analyze
description: Code2Business 遗留系统业务知识还原（完整版）主入口。当用户要求"分析这个代码库""还原业务""生成需求规格""重构前梳理""系统盘点"，或给出代码目录/Git 地址要求产出业务文档时使用。本技能编排完整流程：扫描（c2b-scan）→ 当前 Agent 语义标注（c2b-semantic）→ 交付文档（c2b-docs）。默认不需要外部模型 Key；云端 Provider 是可选路径。触发词：代码分析、业务还原、需求规格、系统盘点、重构前、逆向、遗留系统。
---

# Code2Business 完整流程（主入口）

把旧代码库还原为业务资产，产出与网页端相同的结果。**必须按以下三个阶段顺序执行**，每阶段完成后向用户汇报进度。

## 快速通道（v0.4+，用户只想要结果时）

```bash
c2b pipeline <代码目录> --name <项目名> --out ./deliverables
```

一条命令完成 扫描 → AI 标注（云端 Provider）→ 模块规格 → 全部 7 类交付文档落盘
（`--no-ai` 只做确定性扫描；`--official` 仅含人工确认知识）。默认输出仍需向用户
展示关键统计与待确认项；需要 Agent 级语义质量或无云端 Key 时，走下面的三阶段流程。

## 前置检查

1. 确认 c2b 已安装：`c2b --help`（若失败：
   - 若本机由 install.sh 安装过：先 `export PATH="$HOME/.c2b/venv/bin:$PATH"`
   - 否则见 skill/README.md 或执行 skill/install.sh）
2. 默认不检查 LLM Key：语义阶段使用当前 Agent 的模型。只有用户要求模块规格、QA 或无人值守云端 `label` 时，才检查 `~/.c2b/config.json` 的 Provider 配置。
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
c2b export-evidence <project_id> --output c2b-evidence.json
c2b agent-schema > c2b-semantic-result.json
# 当前 Agent 阅读源码和证据，按模板写入 c2b-semantic-result.json
c2b import-semantic <project_id> --input c2b-semantic-result.json
# 可选云端增强（需配置 Provider）：c2b module-specs <project_id>
```

- 展示导入统计（规则数/对象数/流程数/推演）与待确认项；每条规则必须带扫描内的代码证据。
- 只有用户要求无人值守批量标注时，使用 `c2b label <project_id> --limit 400+`；只有用户要求模块规格时，检查云端 Provider 后执行 `c2b module-specs`。

## 阶段三：交付（调用 c2b-docs）

```bash
c2b docs <project_id> --type combined --fmt word --output 客户交付总报告.docx
c2b docs <project_id> --type overview --output 系统现状说明书.md
c2b docs <project_id> --type rules --output 业务规则清单.md
c2b export-specs <project_id> --output module-specs.zip
```

- 面向客户/业务人员时优先产出 `combined` 客户交付总报告（一份合并报告，
  含执行摘要、功能、流程、核心规则、验收与待确认）。

## 完成汇报模板

```markdown
## 分析完成报告
- 代码规模：X 行 / Y 文件 / N 模块（档位：XL）
- 业务图谱：M 条规则 / O 个业务对象 / F 条流程 / 推演主线
- 交付物：
  - 客户交付总报告.docx（合并版，推荐先看这份）
  - 系统现状说明书.md
  - 模块需求规格.zip（K 份）
- 待确认项：Q 项（见 questions 表，网页「待确认项」页可处理）
- 提示：若已启动 Web 部署，可在 http://localhost:8080 查看全部结果（需指向同一数据库）
```

## 质量约束

- 每个阶段输出后向用户展示关键数据，不要静默执行
- LLM 结果不可信时（规则数异常少/空），提示用户检查 LLM Key 或重跑
- 涉及大量 LLM 调用的操作（全量标注/全部模块规格）先询问用户
- 中间产物（证据/语义 JSON）只放 `~/.c2b/tmp/`，导入成功自动清理；交付文档才写入用户目录
- 数据统一存 `~/.c2b/c2b.db`；发现旧版残留（当前目录的 c2b.db 等）时用 `c2b clean --yes` 清理（非交互场景必须带 `--yes`）
