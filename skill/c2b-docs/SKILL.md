---
name: c2b-docs
description: Code2Business 文档交付技能：从已生成的业务知识生成交付文档（客户交付总报告/系统现状说明书/功能清单/业务规则清单/数据字典/接口清单/验收清单）与模块需求规格打包。当 c2b-analyze 编排流程需要产出文档、或用户要求"生成交付文档""导出需求文档""客户报告""系统现状说明书""验收清单"时使用。依赖 c2b Python 包（含 python-docx/openpyxl）。触发词：交付文档、导出、总报告、现状说明书、功能清单、规则清单、验收清单、打包。
---

# c2b-docs：业务知识 → 交付文档

从已确认/已生成的业务知识渲染交付文档（需先运行 c2b-scan + c2b-semantic）。
面向客户/业务人员时优先产出 `combined` 客户交付总报告——一份文档讲清整个系统。

## 命令

```bash
# 客户交付总报告：概览/功能/流程/核心规则/数据与入口/验收/待确认 合成一份（推荐首选）
c2b docs <project_id> --type combined --fmt word --output 客户交付总报告.docx
# 单类文档（md/word）
c2b docs <project_id> --type overview --fmt md --output 系统现状说明书.md
c2b docs <project_id> --type rules  --fmt word --output 业务规则清单.docx
# 评审草稿才显式加 --draft，纳入 AI 未确认业务知识与 AI 主线
c2b docs <project_id> --type rules --draft --output 业务规则评审草稿.md

# 模块需求规格打包（重建团队用）
c2b export-specs <project_id> --output module-specs.zip

# 一条龙：扫描 → AI 标注 → 模块规格 → 全部交付文档落盘（v0.4+）
c2b pipeline <代码目录> --name <项目名> --out ./deliverables
```

## 文档类型

| --type | 文档 | 面向 |
|---|---|---|
| combined | 客户交付总报告（合并概览/功能/流程/核心规则/验收/待确认） | 客户与业务人员（首选） |
| overview | 系统现状说明书（规模/模块地图/业务域/入口） | 客户汇报 |
| functions | 功能清单（业务域→业务对象→业务动作） | 客户汇报 |
| rules | 业务规则清单（每张卡片全字段+证据） | 客户汇报/评审 |
| dictionary | 数据字典（表/SQL 读写场景） | 技术交接 |
| apis | 接口清单（批处理入口为主） | 技术交接 |
| acceptance | 验收清单（对象动作勾选项 + 待确认项） | 验收 |

## 执行流程

1. 确认 `c2b status <project_id>` 的标注已完成（文档数据来自业务知识）
2. 优先生成 `combined` 客户交付总报告（word）；内部评审用 `--draft` 草稿版
3. 按需补充专项文档：overview + functions + rules，必要时 dictionary / apis / acceptance
4. 模块规格打包：`c2b export-specs`
5. 向用户列出产出文件清单

## 注意事项

- 正式文档只纳入人工确认的业务域、对象、流程和「已确认/已修改」规则；`--draft` 才纳入 AI 未确认项与 AI 主线，已废弃规则始终排除
- 总报告的"执行摘要"与"程序入口概览"是白话化切片，不暴露文件:行等代码细节；完整字段仍以专项文档为准
- 业务对象、数据表和验收项仍可能来自确定性扫描或 AI 推断，文档会标注「未确认/需确认」；正式交付前仍需人工审核
- 规则清单可用网页端导出 Excel（规则页）补充
- 文档不落库（每次生成），重新生成即可更新
