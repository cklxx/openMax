<p align="center">
  <img src="assets/banner.png" alt="openMax banner" width="100%" />
</p>

<h1 align="center">openMax</h1>

<p align="center">
  <strong>Multi AI Agent orchestration hub</strong><br/>
  Dispatch interactive AI agents across terminal panes — automatically.
</p>

<p align="center">
  <a href="https://pypi.org/project/openmax/"><img src="https://img.shields.io/pypi/v/openmax.svg" alt="PyPI"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
</p>

---

openMax is a lead-agent system that decomposes your task, spawns multiple AI coding agents (Claude Code, Codex, OpenCode, etc.) in one [Kaku](https://github.com/niceda/kaku) terminal window with smart pane layout, monitors their progress, and reports results — all automatically.

## How it works

```
openmax run "Build a blog with Next.js"
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
           │ kaku cli spawn / split-pane
           ▼
   ┌────────────────────────────────────────┐
   │  Kaku Window (auto grid layout)        │
   │  ┌──────────────┬───────────────┐      │
   │  │ claude-code   │ codex         │      │
   │  │ "components"  │ "API routes"  │      │
   │  ├──────────────┼───────────────┤      │
   │  │ claude-code   │ opencode      │      │
   │  │ "tests"       │ "styling"     │      │
   │  └──────────────┴───────────────┘      │
   └────────────────────────────────────────┘
         ↑ click any pane to intervene
```

## Install

```bash
pip install openmax
```

### Prerequisites

- **macOS** (Kaku is macOS only for now)
- Python 3.10+
- [Kaku](https://github.com/niceda/kaku) terminal — auto-detected, prompts `brew install --cask kaku` if missing
- At least one AI agent CLI installed:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`)
  - [Codex](https://github.com/openai/codex) (`codex`)
  - [OpenCode](https://github.com/opencode-ai/opencode) (`opencode`)

## Usage

### Run a task

```bash
openmax run "Build a blog with Next.js"
```

The lead agent will:
1. Restate and clarify your goal
2. Decompose it into parallelizable sub-tasks
3. Open one Kaku window with smart grid layout — each agent gets its own pane
4. Monitor each agent's output and intervene if needed
5. Report completion with cost/token summary

### Options

```bash
# Specify working directory
openmax run "Refactor the API module" --cwd /path/to/project

# Use a specific model for the lead agent
openmax run "Write unit tests" --model claude-sonnet-4-20250514

# Limit agent loop turns
openmax run "Fix the bug" --max-turns 30

# Keep agent panes open after completion (don't auto-close)
openmax run "Explore the codebase" --keep-panes
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

All agents run interactively — click into any pane and type to intervene directly.

## Architecture

```
src/openmax/
├── cli.py              # CLI entry point (click)
├── lead_agent.py       # Lead agent: task decomposition + monitoring
│                       #   Uses claude-agent-sdk with custom MCP tools
├── pane_manager.py     # Kaku window/pane lifecycle management
├── kaku.py             # Kaku detection + auto-install
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
| `dispatch_agent` | Dispatch an agent into a pane (shared window, auto grid layout) |
| `read_pane_output` | Read an agent's terminal output to check progress |
| `send_text_to_pane` | Send text to an agent (follow-up instructions, intervention) |
| `list_managed_panes` | List all managed panes and their states |
| `mark_task_done` | Mark a sub-task as completed |
| `report_completion` | Report overall completion percentage |

### Lifecycle

- All agents share one Kaku window with smart grid layout (auto split right/bottom)
- Window auto-resizes to 68% of screen on creation
- On completion or Ctrl-C, all managed panes are automatically closed
- SIGINT, SIGTERM, and atexit handlers ensure clean shutdown
- Use `--keep-panes` to preserve agent panes after the session ends

## License

MIT
