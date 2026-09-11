# C2B Agent Skills（产品③）

> 当前正式版本：[v0.4.0](https://github.com/imlao17/Code2Business/releases/tag/v0.4.0)。完整变化见[仓库变更日志](../CHANGELOG.md)。

这是面向 Claude Code、Codex、opencode 等兼容 Agent 宿主的独立技能产品。它让使用者通过自然语言编排“扫描 → 语义标注 → 模块规格 → 文档交付”，并要求 Agent 在每个阶段报告证据、覆盖范围和待确认项。

**它解决什么问题？** 当团队已经在用 AI 编程 Agent，希望不记命令、又不希望 Agent 跳过扫描或凭空总结代码时，使用 Agent Skills。

它可以独立安装，**不需要部署 Web 工作台，也不要求额外安装 CLI**；安装脚本会安装所需的 c2b wheel 运行时。Web 与 CLI 只是可选的同数据源入口。三种产品的选择方式见根目录 [README](../README.md)。

Skills 当前负责静态扫描、语义还原和文档交付，不会把“Agent 读过代码”伪装成运行证据。若与 Web 共用 PostgreSQL，可在 Web 中为生成的规则、流程和推演分别录入 R1 测试、R2 受控/预发布、R3 生产证据；未录入时为 R0。

## 技能组

| 技能 | 职责 | 被调用方 |
|---|---|---|
| **c2b-analyze** | 主入口：编排全流程，向用户汇报 | 用户直接触发 |
| c2b-scan | 扫描：代码 → 代码图谱（确定性） | c2b-analyze |
| c2b-semantic | 语义：图谱 → 业务知识 + 模块规格 | c2b-analyze |
| c2b-docs | 文档：业务知识 → 交付文档 | c2b-analyze |

## 安装

```bash
# 拷贝本目录到目标机器（自带 wheel），然后：
cd skill && ./install.sh [目标skills目录]   # 默认 ~/.agents/skills

# install.sh 自动完成：
#   1. 创建 ~/.c2b/venv 并安装自带 vendor/*.whl（c2b CLI 运行时）
#   2. 复制 4 个运行时技能 + 1 个零依赖参考包到目标目录
#   3. 生成可选的 LLM 配置模板 ~/.c2b/config.json（仅云端增强功能需要 Key）
#   4. 提示把 venv 加入 PATH
```

安装时需要能访问 PyPI 或内部 Python 镜像；可通过 `PIP_INDEX_URL` 指定镜像。`vendor/` 内置的是 c2b 应用本体，不是所有第三方包的离线集合。

最后把 c2b 加入 PATH（`install.sh` 会提示具体命令）：

```bash
export PATH="$HOME/.c2b/venv/bin:$PATH"
```

若你的 Agent 宿主使用其他技能目录，把该目录作为参数传入即可。例如某些 Codex 环境可以使用：

```bash
./install.sh "$HOME/.codex/skills"
```

## 使用

在 Agent 中直接说：

```
用 c2b 分析 /path/to/code 这个代码库
```

c2b-analyze 自动执行三个阶段，每阶段汇报进度：

```
① c2b-scan     → 规模报告 + 代码图谱
② c2b-semantic → 规则卡片/业务对象/推演/模块需求规格
③ c2b-docs     → 交付文档 + 完成报告
```

### 默认模型：当前 Agent（无需外部 Key）

`c2b-semantic` 默认采用 **Agent Native**：它先导出确定性代码图谱证据，随后由正在工作的
Codex、Claude Code 或其他兼容 Agent 结合源码生成结构化结果，再通过 `c2b import-semantic`
校验文件、行号和方法后入库。因此不需要为 C2B 额外配置 OpenAI、Claude 或其他 API Key。

这不是让 Agent 绕过证据直接写数据库：规则若不引用本次扫描中的方法和行号，整个导入会被拒绝。
已人工确认或修改的内容不会被覆盖。模块规格和图谱问答是单独的云端 LLM 功能；如需它们，再配置
OpenAI Compatible 或 Anthropic Native Provider。

Agent Native 的结构化校验保证“结论能回到代码证据”，不自动证明业务描述正确。对外交付前应按
[质量基线](../docs/quality-baseline.md)抽取至少 50 条规则，由独立评审人登记正确性和修订原因。

一个完整请求示例：

```text
用 c2b 分析 /path/to/legacy-code：先给我规模报告；确认后扫描完整代码；
再输出业务规则、模块需求规格和系统现状说明书，并单列待确认项。
```

也可直接命令行使用底层 c2b（命令见 `cli/README.md`）。

补充说明：
- 若只想做秒级规模探测，不要把 `c2b scan --probe-only` 当作已完成扫描；它只返回规模信息与可复用的 `project_id/scan_id`
- 确认继续后，使用 `c2b scan <代码目录> --name <项目名> --project-id <id>` 进入正式扫描，避免重复创建项目
- 安装脚本会同时复制 `code2business/` 参考包，里面的 `scripts/probe.py` 可用于零依赖规模探测

## 依赖

| 依赖 | 说明 | 安装方 |
|---|---|---|
| Python 3.11+ | 运行时 | 目标机器 |
| c2b wheel（vendor/ 内置） | c2b 应用本体 | install.sh 自动 |
| Python 第三方包 | 扫描/API/文档等运行时依赖 | pip 从 PyPI 或指定镜像安装 |
| LLM API | 可选：模块规格/问答或无人值守 `label` | ~/.c2b/config.json |
| Agent 工具 | Claude Code / opencode | 宿主 |

## 哪些情况选它，哪些情况不选它？

| 情况 | 建议 |
|---|---|
| 希望通过对话驱动全流程，并要求逐阶段解释 | 选 Agent Skills |
| 需要客户演示、人工规则确认和可视化图谱 | 选 Web 工作台 |
| 需要 CI、批量、无人值守脚本 | 选 CLI |
| 只需一次零依赖规模探测 | 使用本包的 `code2business/scripts/probe.py` |

## 零依赖参考版

`code2business/` 为纯提示词参考版（不调 c2b，Agent 直读代码），适合中小项目（<20 万行）与演示场景，也包含 `scripts/probe.py` 这类零依赖辅助脚本。

## 目录

```
skill/
├── install.sh          # 一键安装
├── vendor/             # 内置 c2b 应用 wheel
├── c2b-analyze/        # 主入口
├── c2b-scan/           # 扫描
├── c2b-semantic/       # 语义+规格
├── c2b-docs/           # 文档
├── code2business/      # 零依赖参考版
├── README.md          # 本说明
```
