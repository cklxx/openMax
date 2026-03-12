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

One command, multiple AI agents, one window.

openMax breaks down your task, dispatches agents (Claude Code, Codex, OpenCode) into [Kaku](https://github.com/niceda/kaku) terminal panes, monitors progress, and intervenes when needed.

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
   │  ┌────────────┬─────────────┐  │
   │  │ claude-code │ codex       │  │
   │  │ components  │ API routes  │  │
   │  ├────────────┼─────────────┤  │
   │  │ claude-code │ opencode    │  │
   │  │ tests       │ styling     │  │
   │  └────────────┴─────────────┘  │
   └────────────────────────────────┘
```

## Install

```bash
pip install openmax
```

Requires macOS, Python 3.10+, [Kaku](https://github.com/niceda/kaku) (auto-prompted if missing), and at least one agent CLI (`claude`, `codex`, or `opencode`).

## Usage

```bash
openmax run "Build a blog with Next.js"
openmax run "Refactor the API" --cwd /path/to/project
openmax run "Write tests" --model claude-sonnet-4-20250514
openmax run "Fix the bug" --max-turns 30
openmax run "Explore" --keep-panes
```

```bash
openmax panes              # list panes
openmax read-pane <id>     # read pane output
```

## Agents

| Type | Command |
|------|---------|
| `claude-code` | `claude` |
| `codex` | `codex` |
| `opencode` | `opencode` |

All run interactively. Click any pane to intervene.

## License

MIT
