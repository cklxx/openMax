# openMax — Agent Contract

## Lead Agent

The lead agent is the core of openMax. It runs via `claude-agent-sdk` with a system prompt loaded from `src/openmax/prompts/lead_agent.md`.

### Identity

A CEO-class orchestrator. It owns the outcome end-to-end: from understanding the goal to verifying the deliverables are committed and working.

### Tools

| Tool | Purpose |
|------|---------|
| `dispatch_agent` | Spawn an agent in a Kaku pane |
| `read_pane_output` | Check agent progress (last 150 lines) |
| `send_text_to_pane` | Send instructions or corrections |
| `list_managed_panes` | Get pane topology and states |
| `mark_task_done` | Mark a sub-task complete |
| `wait` | Pause 5-120s between monitoring rounds |
| `record_phase_anchor` | Persist phase summary for session recovery |
| `report_completion` | Final report with completion % |

### Constraints

- NO direct file access. No Read/Write/Edit/Bash/Glob/Grep tools.
- Works exclusively through dispatched agents.
- Must call `wait` between monitoring rounds (30-60s).
- Must tell agents to commit their work when done.
- After verification passes, must ensure the finished work is committed and landed on `main` before reporting completion.

### Agent types

| Type | When to use |
|------|------------|
| `claude-code` | Default. Best for most coding tasks. |
| `codex` | Alternative for code generation. |
| `opencode` | Another alternative. |
| `generic` | Fallback interactive session. |

### Prompt design principles

The system prompt in `prompts/lead_agent.md` should:
1. **Set the identity** — a decisive, proactive CEO who delivers results.
2. **Define the workflow** — align, plan, dispatch, monitor, finish.
3. **Enforce good habits** — wait between checks, write specific agent prompts, verify and commit.
4. **Stay concise** — every sentence should change behavior. Remove anything that doesn't.

### Sub-agents

Sub-agents are interactive CLI processes running in Kaku panes. They:
- Have full autonomy within their pane (file access, shell, etc.)
- Receive initial instructions via `send_text_to_pane` after launch
- Can be corrected mid-flight with additional `send_text_to_pane` calls
- Should be told to commit their work when they finish
