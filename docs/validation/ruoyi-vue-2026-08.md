# RuoYi-Vue 第二样本验证报告（2026-08）

> 对应产品版本：C2B v0.2.0

## 结论

RuoYi-Vue 已作为第二个、与富士通老式 Java 样本技术栈不同的验证样本完成确定性图谱扫描。该样本验证了 Spring MVC 无参/有参 Mapping 注解和 MyBatis XML SQL 的当前实现；它**尚未**完成独立人工语义评审，因此不能据此给出“业务规则准确率”。

## 可复跑对象

| 项目 | 固定值 |
|---|---|
| 上游仓库 | `https://github.com/yangzongzhuan/RuoYi-Vue` |
| 提交 | `b5e14338e83ba4f932b6949bee8767a6a7220dbc` |
| 获取日期 | 2026-08-09 |
| 技术特征 | Spring Boot / Spring MVC / MyBatis XML / Vue |
| 运行方式 | C2B 当前工作区源码，临时 SQLite 数据库 |

## 扫描结果

### 规模探测

| 指标 | 结果 |
|---|---:|
| 代码文件 | 485 |
| 总行数 | 64,975 |
| Java | 34,326 行 |
| Vue | 20,243 行 |
| XML | 2,968 行 |
| SQL | 897 行 |
| 档位 | S（不分片） |

### 修正后正式扫描

| 指标 | 结果 |
|---|---:|
| 已解析源文件 | 297 |
| 模块 | 14 |
| 类 | 278 |
| 方法 | 2,204 |
| 调用边 | 6,254 |
| API 端点 | 147 |
| 数据表 | 32 |
| SQL 操作 | 193 |
| 本机扫描耗时 | 1.21 秒 |

> 耗时仅说明此工作区、此设备、此固定提交和临时 SQLite 数据库下的单次测量；它不是对任何客户项目的性能承诺。

## 发现并修正的问题

第一次扫描已正确提取 2,204 个方法和 6,254 条调用边，但 API 端点为 0、数据表为 2、SQL 操作为 3。对照源码可见该结果不完整：样本后端有 138 处 Mapping 注解，且 `ruoyi-system` 中有 16 个 Mapper XML、136 个 SQL 声明。

根因和修正如下：

| 根因 | 修正 | 验证结果 |
|---|---|---|
| tree-sitter 将 `@RestController`、无参 `@PostMapping` 等表示为 `marker_annotation`，旧扫描器未收集 | 注解提取同时处理 `annotation` 和 `marker_annotation`，并可读取 `value = {...}` 形式的首个路径 | API 从 0 增至 147 |
| 扫描器只解析 Java 字符串 SQL，未处理 MyBatis Mapper XML | 增加 Mapper XML 与 `.sql` 资源的语句级 SQL 提取，动态片段保留为低置信度 SQL 证据 | 表从 2 增至 32，SQL 操作从 3 增至 193 |

这是一项对 Spring/MyBatis 图谱层的兼容性修正，而非语义层“准确率提升”。Mapper XML 当前只产生语句/表级证据，尚未把 XML statement 与 Java Mapper 方法做精确绑定。

## 确定性质量基线

基线清单包含 24 个高价值断言：8 个 API、8 张表、8 项 SQL 读写关系。执行命令：

```bash
PYTHONPATH=core/src python -m c2b.cli evaluate 1 \
  --manifest docs/quality-fixtures/ruoyi-vue-2026-08.json
```

本次修正后结果为 **24 / 24 通过**。详细方法见 [../quality-baseline.md](../quality-baseline.md)。

## 仍未验证或明确受限的能力

- 不包含 Java Mapper Interface 与 XML statement 的精确绑定；
- 不包含 Vue 请求到后端 API 的关联验证；
- 不包含动态 SQL 分支条件的运行时取值验证；
- 不包含任何 LLM/Agent 规则卡片的独立人工抽样；
- 不包含真实 PostgreSQL、Docker Compose、生产数据量和客户网络条件下的性能测试；
- 不应以此报告宣传“所有 Spring/MyBatis 项目均已验证”。
