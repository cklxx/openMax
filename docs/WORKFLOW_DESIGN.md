# openMax 工具注册审查与三阶段工作流设计

## 第一部分：当前工具注册分析

### 工具清单

openMax Lead Agent 通过 `claude-agent-sdk` 的 MCP server 注册了 10 个工具：

| # | 工具名 | 职责 | 评价 |
|---|--------|------|------|
| 1 | `dispatch_agent` | 将子任务派发到终端 pane 中的 AI agent | **核心工具，设计合理。** 处理了 agent 选择、pane 创建、ready 等待、交互式输入等复杂逻辑 |
| 2 | `get_agent_recommendations` | 基于 workspace memory 推荐最适合任务的 agent | **合理但使用率低。** 依赖 memory_store 有足够历史数据才有价值 |
| 3 | `read_pane_output` | 读取 agent pane 的终端输出 | **核心工具，设计合理。** 带有 smart output 提取（错误行上浮） |
| 4 | `send_text_to_pane` | 向 agent pane 发送文本指令 | **核心工具，设计合理。** 用于干预、纠偏、追加指令 |
| 5 | `list_managed_panes` | 列出所有管理中的 pane 状态 | **合理。** 用于 monitoring 阶段的全局视图 |
| 6 | `mark_task_done` | 标记子任务完成 | **合理但被动。** 只修改内存状态，不做任何验证 |
| 7 | `record_phase_anchor` | 记录工作流阶段锚点 | **合理。** 用于 session recovery 和进度跟踪 |
| 8 | `remember_learning` | 存储可复用的经验教训 | **合理。** 跨 session 的知识积累 |
| 9 | `report_completion` | 报告整体完成情况 | **合理。** 终结工具，触发 run summary 持久化 |
| 10 | `wait` | 等待指定秒数 | **必要的节流工具。** 防止过度轮询 |

### 问题分析

#### 1. 工具职责边界问题

- **`mark_task_done` 过于被动**：仅做状态标记，不验证 agent 是否真正完成。Lead Agent 的 system prompt 虽然要求"Review & Verify"，但这完全依赖 LLM 自律——没有工具层面的强制。
- **`report_completion` 承担了太多隐含逻辑**：同时触发 phase anchor 记录、memory 持久化、run summary 写入。职责不够单一，但从使用便利性角度可接受。

#### 2. 缺失的能力

- **无结构化验证工具**：没有工具帮助 Lead Agent 判断"一个任务是否真正完成"。当前完全靠读 pane output 人工判断。
- **无阶段门控机制**：Lead Agent 可以跳过任何阶段，没有工具层面的流程约束。
- **无任务依赖表达**：SubTask 之间没有依赖关系，无法表达"B 依赖 A 完成后才能开始"。
- **无用户确认/交互工具**：Lead Agent 无法在关键节点征求用户意见（如方案确认、验收确认）。

#### 3. 冗余或重叠

- **`record_phase_anchor` vs `report_completion`**：`report_completion` 内部调用了 `_record_phase_anchor("report", ...)`，存在隐含的阶段记录。不算严格冗余，但语义上有重叠。
- **无严格冗余工具**。10 个工具各有明确职责，整体设计精简。

#### 4. System Prompt 与工具的配合

当前 system prompt 定义了 5 个阶段（Align → Plan → Dispatch → Monitor → Review & Verify → Finish），但：
- 阶段之间没有强制过渡机制
- "Review & Verify" 阶段缺乏专用工具支持
- Align 阶段没有与用户交互的工具

---

## 第二部分：三阶段工作流设计

### 设计目标

在现有的 5 阶段管理生命周期之上，增加一个更高层级的**工作流阶段**概念：

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: RESEARCH (调研/PM)                                │
│  ├─ 需求澄清 + 用户对齐                                      │
│  ├─ 技术调研（代码阅读、依赖分析、风险评估）                      │
│  └─ 方案输出 + 用户确认                                       │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: IMPLEMENT (实现)                                  │
│  ├─ 任务分解 + Agent 派发                                     │
│  ├─ 监控 + 干预                                              │
│  └─ 代码提交                                                 │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: VERIFY (验收)                                     │
│  ├─ 自动化测试（lint, test, build）                            │
│  ├─ 交叉审查（多 agent 输出一致性）                             │
│  └─ 验收报告 + 用户确认                                       │
└─────────────────────────────────────────────────────────────┘
```

### 设计原则

1. **渐进式引入**：不破坏现有工具，通过新增工具和 prompt 调整实现
2. **软约束优先**：通过 prompt 引导而非代码硬编码来强制流程
3. **用户可跳过**：用户可以通过 `--skip-research` 或 `--skip-verify` 跳过阶段
4. **工具层面可感知**：新增工具让 Lead Agent 能表达和记录阶段转换

### 新增工具设计

#### 工具 1: `transition_phase`

**目的**：显式声明工作流阶段转换，替代当前隐含的 `record_phase_anchor`。

```python
@tool(
    "transition_phase",
    "Transition the workflow to a new high-level phase. "
    "Valid phases: research, implement, verify. "
    "Must provide a summary of the previous phase's output before transitioning.",
    {
        "from_phase": str,    # 当前阶段
        "to_phase": str,      # 目标阶段
        "gate_summary": str,  # 阶段产出摘要（门控条件）
        "artifacts": list,    # 阶段产出物列表（如：方案文档路径、测试报告）
    },
)
```

**行为**：
- 记录 phase anchor
- 更新 dashboard 显示当前大阶段
- 如果配置了 `--require-approval`，暂停等待用户确认
- 记录 session event `workflow.phase_transition`

#### 工具 2: `request_user_input`

**目的**：让 Lead Agent 在关键节点征求用户意见。

```python
@tool(
    "request_user_input",
    "Request input or confirmation from the user. "
    "Use at phase gates or when the goal is ambiguous.",
    {
        "question": str,         # 要问用户的问题
        "context": str,          # 提供给用户的上下文
        "options": list | None,  # 可选的选项列表
        "phase": str,            # 当前阶段
    },
)
```

**行为**：
- 在终端显示问题和选项
- 阻塞等待用户输入（或超时使用默认值）
- 返回用户回答给 Lead Agent
- 这是实现"方案确认"和"验收确认"的基础

#### 工具 3: `run_verification`

**目的**：在验收阶段执行结构化验证。

```python
@tool(
    "run_verification",
    "Run a structured verification check in a pane. "
    "Returns pass/fail status with details.",
    {
        "check_type": str,   # "test" | "lint" | "build" | "custom"
        "command": str,      # 要执行的命令
        "pane_id": int | None,  # 复用现有 pane 或新建
        "timeout": int,      # 超时秒数
    },
)
```

**行为**：
- 在指定 pane 中执行验证命令
- 轮询输出直到完成或超时
- 解析输出判断 pass/fail
- 返回结构化结果 `{status: "pass"|"fail", output: str, duration: int}`
- 记录到 session event `verification.result`

#### 工具 4: `submit_research_finding`

**目的**：在调研阶段记录结构化的调研发现。

```python
@tool(
    "submit_research_finding",
    "Record a structured research finding during the research phase.",
    {
        "category": str,     # "requirement" | "risk" | "dependency" | "design_decision"
        "title": str,
        "detail": str,
        "confidence": int,   # 1-10
        "source": str,       # 信息来源
    },
)
```

**行为**：
- 收集调研阶段的结构化发现
- 在 research → implement 转换时汇总为实现计划的输入
- 持久化到 session events

### 现有工具修改

#### `dispatch_agent` — 增加 `phase` 参数

```python
# 新增参数
"phase": str,  # "research" | "implement" | "verify"
```

让 dispatch 感知当前阶段，在调研阶段派发的 agent 使用不同的 prompt 模板（强调阅读和分析而非修改）。

#### `mark_task_done` — 增加验证要求

```python
# 新增可选参数
"verification_notes": str | None,  # 完成验证的说明
"verified_by": str | None,        # "auto_test" | "manual_review" | "cross_check"
```

在 verify 阶段，要求提供验证说明才能标记完成。

### System Prompt 调整

在 `src/openmax/prompts/lead_agent.md` 中，将现有 6 步流程替换为三大阶段+子步骤：

```markdown
## How you work

### Phase 1: Research (调研)
Skip this phase ONLY if the task is trivially clear (single bug fix, one-line change).

1. **Understand**: Read the goal. If ambiguous, use `request_user_input` to clarify.
2. **Investigate**: Dispatch agent(s) to read relevant code, check dependencies, identify risks.
   - Prompt agents to ONLY READ and ANALYZE, not modify code.
   - Use `submit_research_finding` for each key discovery.
3. **Propose**: Synthesize findings into a concrete implementation plan.
   - Use `request_user_input` to present the plan and get user approval.
4. **Gate**: Call `transition_phase` from "research" to "implement" with the approved plan.

### Phase 2: Implement (实现)
This is the current core workflow — plan, dispatch, monitor.

1. **Decompose**: Break the approved plan into 1-4 independent sub-tasks.
2. **Dispatch**: Send all independent tasks simultaneously via `dispatch_agent`.
3. **Monitor**: `wait` → `read_pane_output` → intervene if stuck → `mark_task_done` when complete.
4. **Gate**: Call `transition_phase` from "implement" to "verify" with list of changes made.

### Phase 3: Verify (验收)
NEVER skip this phase.

1. **Automated checks**: Use `run_verification` for lint, tests, build.
2. **Cross-check**: If multiple agents worked, verify integration consistency.
3. **Fix**: If any check fails, send agents back to fix via `send_text_to_pane`.
4. **Report**: Call `report_completion` with verified results.
5. **Gate**: If `--require-approval`, use `request_user_input` for final sign-off.
```

### CLI 入口修改

在 `cli.py` 的 `run` 命令中增加选项：

```python
@click.option("--skip-research", is_flag=True, help="Skip research phase, go straight to implementation")
@click.option("--skip-verify", is_flag=True, help="Skip verification phase (not recommended)")
@click.option("--require-approval", is_flag=True, help="Require user approval at phase gates")
```

这些选项通过 prompt 注入传递给 Lead Agent（不需要代码层面的硬编码控制）。

---

## 第三部分：具体代码修改建议

### 文件修改列表

| 优先级 | 文件 | 修改内容 |
|--------|------|----------|
| P0 | `src/openmax/prompts/lead_agent.md` | 重写为三阶段工作流 prompt |
| P0 | `src/openmax/lead_agent.py` | 新增 `transition_phase`, `request_user_input` 工具 |
| P1 | `src/openmax/lead_agent.py` | 新增 `run_verification`, `submit_research_finding` 工具 |
| P1 | `src/openmax/lead_agent.py` | 修改 `dispatch_agent` 增加 `phase` 参数 |
| P1 | `src/openmax/session_runtime.py` | `ContextBuilder` 支持新 event 类型的重建 |
| P2 | `src/openmax/cli.py` | 增加 `--skip-research`, `--skip-verify`, `--require-approval` |
| P2 | `src/openmax/lead_agent.py` | 修改 `mark_task_done` 增加验证参数 |
| P2 | `src/openmax/dashboard.py` | Dashboard 显示当前大阶段 |
| P3 | `src/openmax/lead_agent.py` | `_build_lead_prompt` 注入阶段跳过/审批配置 |
| P3 | `tests/` | 新增三阶段工作流相关测试 |

### 实现顺序建议

**Sprint 1（核心）**：
1. 重写 system prompt（P0，纯文本改动，零风险）
2. 新增 `transition_phase` 工具（P0）
3. 新增 `request_user_input` 工具（P0）

**Sprint 2（增强）**：
4. 新增 `run_verification` 工具（P1）
5. 新增 `submit_research_finding` 工具（P1）
6. 修改 `dispatch_agent` 增加 phase 感知（P1）
7. `ContextBuilder` 支持新 event 类型（P1）

**Sprint 3（打磨）**：
8. CLI 选项（P2）
9. Dashboard 增强（P2）
10. `mark_task_done` 增强（P2）
11. 测试覆盖（P3）

### 关键设计决策

1. **软约束 vs 硬约束**：选择软约束（prompt 引导）。硬约束（代码层面阻止跳过阶段）会限制灵活性，且对简单任务是不必要的摩擦。
2. **`request_user_input` 的阻塞模型**：使用终端 stdin 读取，类似 `click.prompt()`。在 async 上下文中通过 `anyio.to_thread.run_sync` 包装。
3. **调研阶段复用 `dispatch_agent`**：不新建专门的 "research dispatch"，而是通过 `phase` 参数让现有工具适配。调研阶段的 agent 只是收到不同的 prompt（强调只读分析）。
4. **`run_verification` vs 复用 `dispatch_agent` + `read_pane_output`**：选择新增专用工具，因为验证需要结构化的 pass/fail 结果，而不是自由文本输出。

---

## 附录：工具交互流程图

```
用户输入 Goal
    │
    ▼
┌─ RESEARCH ──────────────────────────┐
│  request_user_input (澄清需求)       │
│  dispatch_agent (phase=research)     │
│  read_pane_output (收集调研结果)      │
│  submit_research_finding (结构化记录) │
│  request_user_input (方案确认)       │
│  transition_phase → implement        │
└──────────────────────────────────────┘
    │
    ▼
┌─ IMPLEMENT ─────────────────────────┐
│  dispatch_agent (phase=implement)    │
│  wait + read_pane_output (监控循环)  │
│  send_text_to_pane (干预/纠偏)       │
│  mark_task_done (标记完成)           │
│  transition_phase → verify           │
└──────────────────────────────────────┘
    │
    ▼
┌─ VERIFY ────────────────────────────┐
│  run_verification (lint/test/build)  │
│  read_pane_output (检查结果)         │
│  send_text_to_pane (修复失败项)      │
│  request_user_input (最终确认)       │
│  report_completion (完成报告)        │
└──────────────────────────────────────┘
```
