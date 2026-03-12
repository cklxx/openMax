# openMax

Multi AI Agent orchestration hub — dispatch interactive AI agents across terminal panes.

openMax is a lead-agent system that decomposes your task, spawns multiple AI coding agents (Claude Code, Codex, OpenCode, etc.) in separate [Kaku](https://github.com/niceda/kaku) terminal windows, monitors their progress, and reports results — all automatically.

## How it works

```
openmax run "用 Next.js 写个博客"
         │
         ▼
┌──────────────────────────────────────────────┐
│  Lead Agent (claude-agent-sdk)               │
│  Phase 1: Align goal                         │
│  Phase 2: Plan & decompose                   │
│  Phase 3: Dispatch agents                    │
│  Phase 4: Monitor & correct                  │
│  Phase 5: Summarize & report                 │
└──────────┬───────────────────────────────────┘
           │ kaku spawn --new-window
           ├──────────────┬──────────────┐
           ▼              ▼              ▼
   ┌──────────────┐ ┌──────────┐ ┌──────────┐
   │ Window 1      │ │ Window 2  │ │ Window 3  │
   │ claude-code   │ │ codex     │ │ claude    │
   │ "写组件"      │ │ "写API"   │ │ "写测试"  │
   └──────────────┘ └──────────┘ └──────────┘
         ↑              ↑              ↑
     interactive    interactive    interactive
     (用户可直接点进任意窗口干预 agent)
```

## Install

```bash
pip install openmax
```

or

```bash
uv pip install openmax
```

### Prerequisites

- Python 3.10+
- [Kaku](https://github.com/niceda/kaku) terminal (based on WezTerm)
- At least one AI agent CLI installed:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`)
  - [Codex](https://github.com/openai/codex) (`codex`)
  - [OpenCode](https://github.com/opencode-ai/opencode) (`opencode`)

## Usage

### Run a task

```bash
openmax run "用 Next.js 写一个博客系统"
```

The lead agent will:
1. Restate and clarify your goal
2. Decompose it into parallelizable sub-tasks
3. Open a new Kaku window for each sub-task with the appropriate agent
4. Monitor each agent's output and intervene if needed
5. Report completion with cost/token summary

### Options

```bash
# Specify working directory
openmax run "重构 API 模块" --cwd /path/to/project

# Use a specific model for the lead agent
openmax run "写单元测试" --model claude-sonnet-4-20250514

# Limit agent loop turns
openmax run "修复 bug" --max-turns 30
```

### List panes

```bash
openmax panes
```

### Read a pane's output

```bash
openmax read-pane <pane_id>
```

## Supported agents

| Agent | Type | Mode | Command |
|-------|------|------|---------|
| Claude Code | `claude-code` | Interactive | `claude` |
| Codex | `codex` | Interactive | `codex` |
| OpenCode | `opencode` | Interactive | `opencode` |
| Generic | `generic` | Interactive | `claude` |

All agents run interactively — you can click into any Kaku window and type to intervene directly.

## Architecture

```
src/openmax/
├── cli.py              # CLI entry point (click)
├── lead_agent.py       # Lead agent: task decomposition + monitoring
│                       #   Uses claude-agent-sdk with custom MCP tools
├── pane_manager.py     # Kaku window/pane lifecycle management
├── kaku.py             # Kaku CLI availability check
└── adapters/
    ├── base.py             # AgentAdapter ABC + AgentCommand
    ├── claude_code.py      # Claude Code adapter
    ├── codex_adapter.py    # Codex adapter
    ├── opencode_adapter.py # OpenCode adapter
    └── subprocess_adapter.py # Generic CLI adapter
```

### Lead agent tools

The lead agent has 6 custom tools (via in-process SDK MCP server):

| Tool | Description |
|------|-------------|
| `dispatch_agent` | Open a new Kaku window and start an agent with a prompt |
| `read_pane_output` | Read an agent's terminal output to check progress |
| `send_text_to_pane` | Send text to an agent (follow-up instructions, intervention) |
| `list_managed_panes` | List all managed panes and their states |
| `mark_task_done` | Mark a sub-task as completed |
| `report_completion` | Report overall completion percentage |

### Lifecycle

- On completion or Ctrl-C, all managed Kaku windows/panes are automatically closed
- SIGINT and SIGTERM are handled for clean shutdown
- The `PaneManager` tracks window IDs, pane states (idle/running/done/error), and handles cleanup

## License

MIT
