<p align="center">
  <img src="assets/banner.png" alt="openMax" width="100%" />
</p>

<h1 align="center">openMax</h1>

<p align="center">
  <strong>Multi AI Agent orchestration hub</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/openmax/"><img src="https://img.shields.io/pypi/v/openmax.svg" alt="PyPI"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
</p>

---

One command. Multiple AI agents. One window.

openMax decomposes your task, dispatches AI coding agents (Claude Code, Codex, OpenCode) into [Kaku](https://github.com/niceda/kaku) terminal panes, monitors progress, intervenes when needed, and reports back.

```
openmax run "Build a blog with Next.js"
         │
         ▼
┌──────────────────────────────────┐
│  Lead Agent                      │
│  Align → Plan → Dispatch →       │
│  Monitor → Report                │
└──────────┬───────────────────────┘
           ▼
   ┌────────────────────────────────┐
   │  Kaku Window (auto grid)       │
   │  ┌────────────┬─────────────┐  │
   │  │ claude-code │ codex       │  │
   │  │ components  │ API routes  │  │
   │  ├────────────┼─────────────┤  │
   │  │ claude-code │ opencode    │  │
   │  │ tests       │ styling     │  │
   │  └────────────┴─────────────┘  │
   └────────────────────────────────┘
     ↑ click any pane to intervene
```

## Install

```bash
pip install openmax
```

Requires **macOS**, Python 3.10+, and [Kaku](https://github.com/niceda/kaku) terminal (auto-prompted if missing).

## Usage

```bash
openmax run "Build a blog with Next.js"
openmax run "Refactor the API module" --cwd /path/to/project
openmax run "Write unit tests" --model claude-sonnet-4-20250514
openmax run "Fix the bug" --max-turns 30
openmax run "Explore the codebase" --keep-panes
```

```bash
openmax panes              # list all panes
openmax read-pane <id>     # read a pane's output
```

## Supported agents

| Agent | Type | Command |
|-------|------|---------|
| Claude Code | `claude-code` | `claude` |
| Codex | `codex` | `codex` |
| OpenCode | `opencode` | `opencode` |
| Generic | `generic` | `claude` |

All interactive — click any pane to intervene.

## Architecture

```
src/openmax/
├── cli.py           # CLI (click)
├── lead_agent.py    # Lead agent + 6 custom MCP tools
├── pane_manager.py  # Window/pane lifecycle
├── kaku.py          # Kaku detection + auto-install
└── adapters/        # Agent CLI adapters
```

### Lead agent tools

| Tool | What it does |
|------|-------------|
| `dispatch_agent` | Spawn agent in a pane (auto grid layout) |
| `read_pane_output` | Read agent terminal output |
| `send_text_to_pane` | Send instructions to agent |
| `list_managed_panes` | List panes and states |
| `mark_task_done` | Mark sub-task done |
| `report_completion` | Report completion % |

### Lifecycle

- One window, smart grid layout, auto-resized to 68% screen
- Clean shutdown on exit / Ctrl-C / SIGTERM
- `--keep-panes` to preserve panes after session

## License

MIT
