---
name: c2b-semantic
description: Code2Business 语义标注技能：把代码图谱还原为业务知识（业务域/业务对象/规则卡片/业务流程/业务推演/模块需求规格）。当 c2b-analyze 编排流程需要语义分析、或用户要求"识别业务规则""业务对象""业务推演""模块需求规格"时使用。调用 DeepSeek API（复用 ~/.c2b/config.json 配置），所有结论带代码证据与可信度。依赖 c2b Python 包。触发词：语义标注、业务规则、业务对象、业务域、推演、需求规格。
---

# c2b-semantic：图谱 → 业务知识

AI 语义层：基于已落库的代码图谱（需先运行 c2b-scan），调用 LLM 识别业务知识。

## 命令

```bash
c2b label <project_id> --limit 100 [--concurrency 16] [--model deepseek-chat]
c2b module-specs <project_id> [--module <id>] [--name <模块名>] [--force]
```

- `label`：一次完成 业务域(S1) → 业务对象(S2) → 规则卡片(S3) → 流程(S4) → 业务推演(S6)
- `module-specs`：模块级需求规格（功能点/流程/数据模型/规则/信息缺口）
- `--force`：绕过 LLM 缓存完全重新生成
- 推荐模型 `deepseek-chat`（快 3-5 倍，网页端同款）

## 执行顺序

1. **先看状态**：`c2b status <project_id>`——若"AI 标注"未完成，执行 `c2b label`
2. **规则标注**：`c2b label <project_id> --limit 100`（小规模试跑）
   - 展示：规则数/对象数/流程数/推演主线
   - 质量好则建议用户跑全量：`--limit 400`（约 15-25 分钟）
3. **模块需求规格**：
   - 单模块试点：`c2b module-specs <project_id> --name Sms01206`
   - 全量：`c2b module-specs <project_id>`（130 模块约 30-60 分钟，先询问用户）
   - 打包：`c2b export-specs <project_id>`

## 结果解读

| 产出 | 说明 |
|---|---|
| business_rules | 规则卡片：类型/逻辑/状态数据变化/证据文件:行/可信度/确认状态 |
| business_objects | 业务对象（实体级，如销售订单→order_tbl） |
| business_flows | 业务流程（入口+调用链→业务步骤） |
| business_chains | 业务推演：主线 + 驱动关系（V1-V4 核实等级；V0 只进入推断假设） |
| module_analyses | 模块需求规格（含信息缺口清单） |

## 质量约束

- 规则数异常少（<候选数 10%）时检查：候选选择策略（工具方法过滤）是否生效
- 业务域为"未归类"时提示用户重跑（分批聚类已内置）
- 所有结论都有证据字段；人工确认在网页端「流程与规则」页完成
