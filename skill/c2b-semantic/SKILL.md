---
name: c2b-semantic
description: Code2Business 语义标注技能：把代码图谱还原为业务知识（业务域/业务对象/规则卡片/业务流程/业务推演/模块需求规格）。默认复用当前 Agent 的模型，通过证据导出和结构化校验入库，无需外部 API Key；云端 Provider 为可选替代。所有结论带代码证据与可信度。依赖 c2b Python 包。触发词：语义标注、业务规则、业务对象、业务域、推演、需求规格。
---

# c2b-semantic：图谱 → 业务知识

语义层基于已落库的代码图谱（需先运行 c2b-scan）。**默认由当前 Agent 识别业务知识**，不调用外部 API；只有用户明确要求无人值守、批量云端运行，或需要模块规格/QA 时，才使用配置的云端 Provider。

## 命令

```bash
c2b export-evidence <project_id> --output c2b-evidence.json
c2b agent-schema > c2b-semantic-result.json
c2b import-semantic <project_id> --input c2b-semantic-result.json
c2b label <project_id> --limit 100 [--concurrency 16] [--model deepseek-chat]  # 可选云端路径
c2b module-specs <project_id> [--module <id>] [--name <模块名>] [--force]
```

- `export-evidence`：从已完成扫描导出模块、方法、接口、表及证据行号，不含模型 Key
- `agent-schema`：输出 Agent Native JSON 模板
- `import-semantic`：校验每条规则的文件/行号/方法后，原子写入域、对象、规则、流程、推演和待确认项
- `label`：可选云端路径，一次完成 业务域(S1) → 业务对象(S2) → 规则卡片(S3) → 流程(S4) → 业务推演(S6)
- `module-specs`：模块级需求规格（功能点/流程/数据模型/规则/信息缺口）
- `--force`：绕过 LLM 缓存完全重新生成
- 推荐模型 `deepseek-chat`（快 3-5 倍，网页端同款）

## 执行顺序

1. **先看状态**：`c2b status <project_id>`——必须已有完成扫描。
2. **Agent Native（默认）**：执行 `c2b export-evidence <project_id> --output c2b-evidence.json`，阅读导出的证据和项目源码；执行 `c2b agent-schema` 取得模板，生成 `c2b-semantic-result.json`。不得编造规则：每条规则必须填写扫描内方法、文件和覆盖该规则的行号。然后执行 `c2b import-semantic <project_id> --input c2b-semantic-result.json`。
   - 展示：规则数/对象数/流程数/推演主线
   - 若导入拒绝，修正 JSON 中的证据或删除无法证实的结论后重试；禁止跳过校验直接写库。
3. **可选云端标注**：仅当用户明确选择外部 Provider 时，执行 `c2b label <project_id> --limit 100`；质量好可建议 `--limit 400`。
4. **模块需求规格**（需要云端 Provider）：
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
