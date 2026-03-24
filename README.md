<p align="center">
  <img src="assets/banner.png" alt="openMax" width="100%" />
</p>

<h1 align="center">openMax</h1>

<p align="center">
  <strong>One command. Multiple AI agents. Zero babysitting.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/openmax/"><img src="https://img.shields.io/pypi/v/openmax.svg" alt="PyPI"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
</p>

---

Stop babysitting. Stop switching windows. Give openMax your tasks and walk away — it orchestrates multiple AI agents ([Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex), [OpenCode](https://github.com/opencode-ai/opencode)) in parallel terminal panes, monitors them, and merges the results. If you already juggle multiple Claude Code windows, openMax replaces that chaos with a single command that manages everything. Built-in **Project Archetypes** give the lead agent domain-aware decomposition strategies — it knows how to split a web app, CLI tool, API service, or library without you telling it.

Works with [Kaku](https://github.com/niceda/kaku) on macOS and [tmux](https://github.com/tmux/tmux) on any platform.

## Architecture

```
openmax run "Build the API" "Add auth" "Write tests"
                        |
                        v
            +-----------------------+
            |      TaskRunner       |
            |  (orchestrates tasks  |
            |   in parallel)        |
            +-----------+-----------+
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
   +-----------+  +-----------+  +-----------+
   |Lead Agent |  |Lead Agent |  |Lead Agent |
   | "Build    |  | "Add      |  | "Write    |
   |  the API" |  |  auth"    |  |  tests"   |
   +-----------+  +-----------+  +-----------+
          |             |             |
          v             v             v
   +-----------+  +-----------+  +-----------+
   | Terminal  |  | Terminal  |  | Terminal  |
   | Panes     |  | Panes     |  | Panes     |
   | (auto     |  | (auto     |  | (auto     |
   |  grid)    |  |  grid)    |  |  grid)    |
   +-----------+  +-----------+  +-----------+
```

## Install

```bash
pip install openmax
```

**Requirements:**

- Python 3.10+
- A terminal backend (one of the following):
  - **macOS:** [Kaku](https://github.com/niceda/kaku) (auto-detected, auto-prompted to install via Homebrew)
  - **macOS / Linux / WSL:** [tmux](https://github.com/tmux/tmux) (auto-detected if you're in a tmux session)
- At least one agent CLI: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`), [Codex](https://github.com/openai/codex) (`codex`), or [OpenCode](https://github.com/opencode-ai/opencode) (`opencode`)

### Terminal Backend Setup

openMax auto-detects the best available backend. On macOS it prefers Kaku; on Linux/WSL it uses tmux. If the required backend is missing, openMax will prompt you to install it.

<details>
<summary><strong>macOS with Kaku (recommended)</strong></summary>

```bash
brew install --cask kaku
# Open Kaku, then run openmax inside it
openmax run "your task"
```

</details>

<details>
<summary><strong>macOS / Linux / WSL with tmux</strong></summary>

```bash
# Install tmux
brew install tmux        # macOS
sudo apt install tmux    # Debian / Ubuntu
sudo dnf install tmux    # Fedora / RHEL
sudo pacman -S tmux      # Arch

# Just run openmax — it creates a tmux session automatically
openmax run "your task"

# Watch agents work (in another terminal)
tmux attach -t openmax
```

openMax auto-creates a detached tmux session named `openmax` and spawns agent panes inside it. If you're already inside a tmux session, openmax uses it directly.

</details>

## Quick Start

```bash
# Run multiple tasks in parallel — one command handles everything
openmax run "Build the API" "Add authentication" "Write tests"

# Run tasks from a file
openmax run -f tasks.txt

# Single task — give it a job and let it work
openmax run "Build a blog with Next.js"

# Specify a working directory
openmax run "Add authentication to the app" --cwd ~/projects/my-app

# Use a specific model for the lead agent
openmax run "Refactor the database layer" --model claude-sonnet-4-20250514

# Keep panes open after completion for manual inspection
openmax run "Fix all failing tests" --keep-panes

# Only use Claude Code and Codex (first one is preferred)
openmax run "Build the API" --agents claude-code,codex

# Force all tasks to use a single agent type
openmax run "Fix lint errors" --agents claude-code
```

## Multi-Task Mode

Run multiple tasks in parallel — each gets its own Lead Agent, its own set of agent panes, and its own isolated branch. TaskRunner coordinates everything from a single command.

```bash
# Three tasks, fully parallel
openmax run "Build the REST API" "Create React components" "Write integration tests"

# Load tasks from a file (one per line)
openmax run -f tasks.txt
```

- Each task runs independently with its own Lead Agent and terminal panes
- TaskRunner handles orchestration, failure isolation, and result merging
- Tasks that fail don't block others — you get partial results instead of nothing
- Perfect for power users who currently juggle multiple Claude Code windows

## Project Management

Track and monitor multiple projects from a single place.

```bash
# Add a project for monitoring
openmax projects add ~/code/my-app

# List all tracked projects
openmax projects list

# Check status across all projects
openmax projects status
```

## Usage

### `openmax run`

The core command. The lead agent (powered by [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)) will:

1. **Align** — clarify your goal, identify scope
2. **Plan** — decompose into parallelizable sub-tasks
3. **Dispatch** — spawn agents into terminal panes (one window, auto grid layout)
4. **Monitor** — read agent output, intervene if stuck or off-track
5. **Review** — cross-check deliverables, run tests, verify integration
6. **Report** — summarize results, ensure changes are committed

```bash
openmax run "Build a REST API with FastAPI and SQLAlchemy"
```

**All options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--cwd PATH` | Working directory for agents | Current directory |
| `--model MODEL` | Model for the lead agent | `claude-sonnet-4-20250514` |
| `--max-turns N` | Max lead agent loop turns | `50` |
| `--keep-panes` | Don't close panes on exit | `false` |
| `--session-id ID` | Persistent session identifier | Auto-generated |
| `--resume` | Resume a persistent session (requires `--session-id`) | `false` |
| `--agents LIST` | Comma-separated allowed agent types, in preference order | All available |

### `openmax doctor`

Check that your environment is ready before running tasks.

```bash
$ openmax doctor
openMax environment check
──────────────────────────────────────────
  ✅  Python              3.11.4
  ✅  Kaku CLI            0.6.0
  ✅  tmux                3.6a  (installed, not in session)
  ✅  claude              2.1.76
  ❌  opencode
       Fix: See https://github.com/opencode-ai/opencode  (optional)
──────────────────────────────────────────
1 issue found.
```

### `openmax panes`

List all terminal panes and their status, or read a specific pane by ID.

```bash
$ openmax panes
Found 4 panes:
  Pane 1: claude-code (120x30) ★
  Pane 2: codex (120x30)
  Pane 3: claude-code (120x30)
  Pane 4: opencode (120x30)

$ openmax panes 1
```

### `openmax memories`

Show learned workspace memory from previous runs.

```bash
# Show memory for the current directory
$ openmax memories

# Show memory for a specific workspace
$ openmax memories --cwd ~/projects/my-app

# Limit number of entries
$ openmax memories --limit 5
```

### `openmax recommend-agents`

Show ranked agent recommendations for a task based on past runs in the workspace.

```bash
$ openmax recommend-agents "Build REST API" --cwd ~/projects/my-app
Agent recommendations for Build REST API
- codex: 12
  Converged fastest on API work in last 3 runs
- claude-code: 8
  Good for general coding tasks
```

### Session Resume

Resume a previous session to continue where you left off:

```bash
# Start a named session
openmax run "Build the frontend" --session-id my-frontend

# Later, resume it
openmax run "Continue the work" --session-id my-frontend --resume
```

## Examples

```bash
# Ship a feature end-to-end (3 parallel tasks)
openmax run "Build the REST API" "Create React components" "Write integration tests"

# Fix multiple issues at once
openmax run "Fix auth bug #123" "Fix pagination #456" "Fix search #789"

# Morning kickoff — start all your day's work
openmax run -f today-tasks.txt

# Full-stack app development
openmax run "Build a todo app with React frontend and Express backend"

# Bug fixing across a codebase
openmax run "Find and fix all TypeScript type errors in src/"

# Code refactoring
openmax run "Migrate all class components to functional components with hooks"

# Testing
openmax run "Write comprehensive unit tests for the utils/ directory"

# Multi-language projects
openmax run "Add Python bindings for the Rust core library"
```

## Supported Agents

| Agent | Command | Notes |
|-------|---------|-------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `claude` | Best for most coding tasks |
| [Codex](https://github.com/openai/codex) | `codex` | OpenAI Codex CLI |
| [OpenCode](https://github.com/opencode-ai/opencode) | `opencode` | OpenCode CLI |

All agents run interactively in their own pane by default. You can click into any pane and type to intervene at any time. The lead agent also monitors and sends corrections automatically.

You can also define your own agents in config, including arbitrary local CLIs or remote entrypoints over SSH.

### Agent Selection

By default, the lead agent automatically picks the best agent for each sub-task (`claude-code` is the default). Use `--agents` to restrict and prioritize built-in or configured agent names:

```bash
# Prefer Codex, fall back to Claude Code if needed
openmax run "Build the API" --agents codex,claude-code

# Only use Claude Code for everything
openmax run "Refactor auth module" --agents claude-code
```

The order matters — the **first agent in the list is the preferred default**. If the lead agent tries to use an agent not in the list, it automatically falls back to the first one.

The lead agent also learns from past runs. After completing a task, it stores what worked well via `remember_learning`. Future runs in the same workspace will receive these recommendations automatically. View them with `openmax memories`.

### Custom Agent Config

openMax merges agents from these locations, in this order:

1. Built-in agents
2. `~/.config/openmax/agents.toml`
3. `<workspace>/.openmax/agents.toml`
4. `OPENMAX_AGENTS_FILE=/path/to/agents.toml`

List the effective agent registry for a workspace with:

```bash
openmax agents --cwd /path/to/repo
```

Example config:

```toml
[agents.remote-codex]
command = ["ssh", "devbox", "bash", "-lc", "cd {cwd_sh} && codex"]
interactive = true
startup_delay = 8

[agents.remote-review]
command = ["ssh", "devbox", "bash", "-lc", "codex exec {prompt_sh}"]
interactive = false
```

Supported placeholders:

- `{cwd}` / `{prompt}`: raw substitution
- `{cwd_sh}` / `{prompt_sh}`: shell-escaped substitution for commands such as `ssh ... "cd ... && tool"`

Notes:

- Interactive agents start the command first, then openMax sends the task prompt into the pane.
- Non-interactive agents must include `{prompt}` or `{prompt_sh}` in `command`.
- Use `startup_delay` for slow-starting commands such as SSH sessions or remote shells.

## How It Works

openMax uses a **lead agent** that has no direct access to files or code. Instead, it orchestrates through MCP tools:

| Tool | Purpose |
|------|---------|
| `dispatch_agent` | Spawn an agent in a terminal pane |
| `read_pane_output` | Check what an agent is doing |
| `send_text_to_pane` | Send follow-up instructions |
| `list_managed_panes` | Get pane states |
| `mark_task_done` | Track sub-task progress |
| `report_completion` | Finalize and report results |

**Cleanup:** On exit (normal completion, Ctrl-C, or SIGTERM), all managed panes are killed automatically. Use `--keep-panes` to keep them open.

## Project Archetypes

openMax recognizes common project types and automatically applies the right decomposition strategy:

| Archetype | Detected when | Decomposition |
|-----------|--------------|---------------|
| **Web App** | "web app", "full-stack", "SPA" | schema → API + frontend (parallel) → auth → tests |
| **CLI Tool** | "CLI", "command-line", "terminal tool" | arg parsing → core logic → output formatting → tests |
| **API Service** | "API", "REST", "microservice" | endpoints → middleware + auth (parallel) → models → tests |
| **Library** | "library", "package", "SDK" | core module → public API → docs + tests → packaging |
| **Refactor** | "refactor", "migrate", "restructure" | analysis → transform → update callers → tests |

Each archetype includes planning hints, common failure modes, and anti-patterns — injected directly into agent briefs.

**Custom archetypes:** Add `.openmax/archetypes/*.yaml` to your project:

```yaml
name: my_archetype
display_name: My Custom Type
description: Custom project type
indicators: ["my-pattern"]
subtask_templates:
  - name: step-1
    description: "First step"
    files_pattern: "src/**/*.py"
    estimated_minutes: 10
planning_hints:
  - "Check X before Y"
anti_patterns:
  - "Don't do Z"
```

## Best Practice: Let Your Agent Call openMax

The most powerful way to use openMax is to **let your AI agent delegate work to it asynchronously**. Instead of running openMax manually, instruct your agent (Claude Code, Cursor, etc.) to spawn openMax as a background process:

```bash
# In your agent's prompt or CLAUDE.md:
"When you need to parallelize work across multiple files or modules,
 run openmax in the background and continue with other tasks."
```

Example workflow — your agent is building a full-stack app:

```bash
# Your agent runs this in the background, then continues its own work
openmax run "Build React components for dashboard" --cwd ./frontend --agents claude-code &

# Meanwhile, your agent works on the backend itself
# ...

# Later, check the results
openmax panes 1
```

This turns a single agent into a **team of agents** — your primary agent handles the high-level architecture while openMax manages the parallel sub-tasks. The key benefits:

- **Async by nature** — openMax runs independently; your agent doesn't block
- **Automatic monitoring** — the lead agent watches all sub-agents, intervenes when stuck
- **Clean separation** — each agent works in its own terminal pane, no conflicts

## License

MIT
