---
name: c2b-scan
description: Code2Business 扫描技能：把代码仓库还原为代码图谱（确定性分析，不依赖 LLM）。当 c2b-analyze 编排流程需要扫描、或用户要求"扫描代码""生成代码图谱""统计代码量""模块地图"时使用。支持百万行级代码库：探测规模→档位判定→分片→tree-sitter 解析→调用图/SQL 提取→图谱落库（与网页端共用数据库）。依赖 c2b Python 包。触发词：扫描、代码图谱、规模统计、模块地图。
---

# c2b-scan：代码 → 代码图谱

确定性分析阶段（无 LLM 参与），产出代码图谱并落库。

## 命令

```bash
c2b scan <代码根目录> --name <项目名> [--module-depth 2] [--probe-only]
```

- 默认直接创建项目并完成正式扫描，返回 `project_id`
- `--probe-only`：只做规模探测（秒级），不解析；会返回 `project_id` / `scan_id` 供后续 `--project-id` 复用，但这 **不是** 已完成扫描

## 执行流程

1. **探测（可选）**：`c2b scan <目录> --name <项目名> --probe-only`
   - 展示：档位（S/M/L/XL）、总行数、文件数、模块 Top、编码分布、历史版本提示
   - 记录 `project_id`，让用户确认后进入正式扫描
2. **全量解析**：`c2b scan <目录> --name <项目名> --project-id <上一步的 project_id>`
   - 247 万行实测约 1-2 分钟；完成后展示 stats（模块/类/方法/调用边/表/SQL）
   - 若无需预探测，也可第一次就直接执行 `c2b scan <目录> --name <项目名>`
3. **验证**：`c2b status <project_id>` 确认"扫描分析 ✅"

## 输出解读

| stats 字段 | 含义 |
|---|---|
| modules / classes / methods | 模块 / 类 / 方法数 |
| call_edges | 调用边（方法级） |
| data_tables / sql_operations | 从字符串 SQL 提取的表 / SQL 操作 |

## 注意事项

- 大仓库（XL 档 >200 万行）会自动分片，串行执行（网页端并行化是后续版本）
- 非 UTF-8（Shift-JIS 等）自动检测转码，不阻塞
- 文件名含 `_old/履歴/移行` 等特征的文件会标记为疑似历史版本；当前扫描仍保留它们以便审计，交付前需结合证据人工筛选
- 若只想做零依赖规模预估，也可使用同包内 `code2business/scripts/probe.py`；它只产出本地报告，不创建 c2b 项目
