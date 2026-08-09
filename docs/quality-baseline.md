# 质量基线与验收方法

> 对应产品版本：C2B v0.2.0（2026-08-09）

本文定义当前 C2B 的质量口径。它将**确定性图谱正确性**与**语义业务结论正确性**分开记录，避免把“有静态证据”误写成“业务结论已验证”。

## 1. 三类证据与人工评审，不混算

| 层级 | 检查对象 | 谁能复跑 | 当前自动化程度 | 可以得出的结论 |
|---|---|---|---|---|
| D 层：确定性图谱 | 方法、调用边、Spring API、MyBatis SQL、表 | 开发者/CI | 自动 | 扫描器是否把已知代码事实写入图谱 |
| S 层：语义业务知识 | 业务对象、规则卡片、流程、推演 | 独立业务/代码评审人 | 人工抽样记录 | 业务描述是否正确、有用且证据充分 |
| R 层：运行验证 | 测试结果、受控环境观测、生产日志/Trace/数据核对 | 测试、运维、业务或开发 | 当前人工录入 | 某项语义结论是否在实际执行中得到支持 |

`V1–V4` 只表示静态证据强度。`R0–R3` 独立表示运行验证：R0 无记录、R1 测试执行、R2 受控/预发布、R3 生产观测。
人工确认状态是第三条独立信任轴。V、R、AI 可信度和人工确认不相互加分，也不能彼此替代。
当前 R 证据由 Web/API 手工录入并保留引用，不代表系统已经自动接入日志、Trace 或 APM。

## 2. 固定样本角色

| 样本 | 技术特征 | 质量角色 | 不用于证明什么 |
|---|---|---|---|
| n-s-modernization-origin | 富士通 APWORKS/JBK、Shift-JIS、247 万行 Java | XL 规模、老式 Java、编码与历史目录适配 | Spring/MyBatis/Vue 泛化能力 |
| RuoYi-Vue | Spring Boot、Spring MVC、MyBatis XML、Vue | 框架化 Java 图谱回归与语义抽样主样本 | 所有 Spring 项目的通用质量 |

RuoYi-Vue 的固定来源、提交和已知代码事实存放在 [quality-fixtures/ruoyi-vue-2026-08.json](quality-fixtures/ruoyi-vue-2026-08.json)。修改样本提交或断言时，必须在评审报告中说明原因，不能静默替换。

## 3. D 层：可复跑图谱基线

准备一个临时数据库，扫描固定提交后执行：

```bash
export C2B_DB_URL='sqlite:////tmp/c2b-quality.db'
PYTHONPATH=core/src python -m c2b.cli scan /path/to/RuoYi-Vue \
  --name RuoYi-Vue --git-commit <固定提交>
PYTHONPATH=core/src python -m c2b.cli evaluate <project-id> \
  --manifest docs/quality-fixtures/ruoyi-vue-2026-08.json \
  --output /tmp/ruoyi-quality-result.json
```

基线命令针对最新完成扫描逐项验证：

- Spring MVC 的 HTTP 方法与路径；
- MyBatis/DDL 中应出现的数据表；
- 指定表的读写操作。

任何一条断言缺失，命令以非零状态结束，并列出缺失项；它适合接入 CI。它不是“准确率百分比”：断言是经过人工选择的高价值回归点，用于发现能力倒退。

## 4. S 层：语义抽样评审

在每个准备对外交付的项目上，抽取不少于 50 条规则卡片，并按规则类型分层覆盖：校验、计算、状态、权限、触发、异常和其他。使用 [quality-fixtures/semantic-review-template.csv](quality-fixtures/semantic-review-template.csv) 逐条登记。

评审人必须能够查看源码和业务上下文，且不应是生成该卡片的同一自动化执行主体。每条给出以下之一：

- `correct`：业务描述、类型与证据都成立；
- `partly_correct`：主体成立，但范围、前置条件、影响或类型需要修订；
- `incorrect`：描述与代码/业务不符；
- `insufficient_evidence`：没有足够代码事实支撑，不应当作结论；
- `not_applicable`：样本不适用，单独说明，不能计为正确。

对外报告至少同时披露：样本数、各 verdict 数量、`correct` 比例、`correct + partly_correct` 比例、按规则类型的分布，以及人工修订的主要原因。没有完成独立评审时，禁止声称“语义准确率”。

## 5. 当前门槛

| 用途 | D 层 | S 层 |
|---|---|---|
| 开发回归 | 固定样本清单 100% 通过 | 不要求每次重评 |
| 新客户试点 | 样本扫描成功，关键 API/SQL 断言按其技术栈补齐 | 至少 50 条独立人工评审，不输出未经审计的准确率 |
| 对外交付 | 本项目 D 层结果归档 | 有抽样结果、修订记录和待确认项清单 |

当前 RuoYi 结果见 [validation/ruoyi-vue-2026-08.md](validation/ruoyi-vue-2026-08.md)。它证明的是当前扫描器对该固定样本的确定性图谱能力，不代表 LLM 语义质量已经完成统计。
