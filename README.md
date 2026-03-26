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

Give openMax your tasks and walk away — it orchestrates multiple AI agents ([Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex), [OpenCode](https://github.com/opencode-ai/opencode)) in parallel terminal panes, monitors them, and merges the results. Built-in **Project Archetypes** give the lead agent domain-aware decomposition strategies — it knows how to split a web app, CLI tool, API service, or library without you telling it.

Works with [Kaku](https://github.com/niceda/kaku) and [Ghostty](https://ghostty.org/) on macOS, and [tmux](https://github.com/tmux/tmux) on any platform.

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
  - **macOS:** [Kaku](https://github.com/niceda/kaku) or [Ghostty](https://ghostty.org/) (auto-detected)
  - **macOS / Linux / WSL:** [tmux](https://github.com/tmux/tmux) (auto-detected)
- At least one agent CLI: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`), [Codex](https://github.com/openai/codex) (`codex`), or [OpenCode](https://github.com/opencode-ai/opencode) (`opencode`)

### Terminal Backend Setup

openMax auto-detects the best available backend: Kaku > Ghostty > tmux. If none is found, it prompts you to install one.

<details>
<summary><strong>macOS with Kaku (recommended)</strong></summary>

```bash
brew install --cask kaku
openmax run "your task"
```

</details>

<details>
<summary><strong>macOS with Ghostty</strong></summary>

```bash
brew install --cask ghostty
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
# Single task
openmax run "Build a blog with Next.js"

# Multiple tasks in parallel
openmax run "Build the API" "Add authentication" "Write tests"

# Read prompt from a file
openmax run @task.md

# Mix file prompts and inline prompts
openmax run @tasks/api.md "Write tests for the API"

# Quality mode — write, review, challenge, rewrite
openmax run "Refactor the auth module" --quality

# Keep panes open after completion
openmax run "Fix all failing tests" --keep-panes

# Restrict to specific agents
openmax run "Build the API" --agents claude-code,codex
```

### File Prompts (`@file`)

Prefix a task argument with `@` to read the prompt from a file. Supports relative paths, absolute paths, and `~` expansion:

```bash
openmax run @task.md                        # relative path
openmax run @tasks/refactor.txt             # subdirectory
openmax run @~/prompts/big-task.md          # home directory
openmax run @/absolute/path/to/prompt.md    # absolute path
openmax run @task1.md @task2.md             # multiple file prompts
openmax run @task.md "also do this"         # mix file and inline
```

## Commands

### `openmax run`

The core command. Decomposes tasks and dispatches sub-agents in terminal panes.

The lead agent (powered by [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)) will:

1. **Plan** — decompose into parallelizable sub-tasks
2. **Dispatch** — spawn agents into terminal panes (one window, auto grid layout)
3. **Monitor** — read agent output, intervene if stuck or off-track
4. **Review** — cross-check deliverables, run tests, verify integration
5. **Report** — summarize results, ensure changes are committed

| Option | Description | Default |
|--------|-------------|---------|
| `--cwd PATH` | Working directory for agents | Current directory |
| `--project NAME` | Project name per task (from registry, repeatable) | — |
| `--model MODEL` | Model for the lead agent | `claude-sonnet-4-20250514` |
| `--max-turns N` | Max lead agent loop turns | Unlimited |
| `--keep-panes` | Don't close panes on exit | `false` |
| `--session-id ID` | Persistent session identifier | Auto-generated |
| `--resume` | Resume a persistent session (requires `--session-id`) | `false` |
| `--agents LIST` | Comma-separated allowed agent types, in preference order | All available |
| `--pane-backend` | Force a backend: `kaku`, `ghostty`, `tmux`, `terminal-tmux`, `headless`, `auto` | Auto-detect |
| `--no-confirm` | Skip interactive plan confirmation (for automation) | `false` |
| `-v, --verbose` | Show detailed subtask output | `false` |
| `-q, --quality` | Quality mode: write, review, challenge, rewrite cycle | `false` |

### `openmax loop`

Run openMax in a continuous loop, pursuing a goal across unlimited iterations. Memory accumulates between iterations — the lead agent discovers new improvements each run.

```bash
# Continuously improve test coverage
openmax loop "Increase test coverage to 90%"

# Cap at 5 iterations with 30s delay between each
openmax loop "Polish the UI" --max-iterations 5 --delay 30
```

### `openmax sessions`

List, inspect, and analyze past sessions.

```bash
# List recent sessions
openmax sessions

# Inspect a specific session
openmax inspect <session-id>

# Show cost and token usage
openmax usage
openmax usage --last 7d

# View or follow a session's message log
openmax log <session-id>
openmax log <session-id> --follow
```

### `openmax clean`

Remove residual artifacts from interrupted or completed runs.

```bash
# Clean branches, worktrees, task files, sockets
openmax clean

# Preview what would be removed
openmax clean --dry-run

# Also expire old sessions
openmax clean --all
```

### `openmax doctor`

Check that your environment is ready.

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

List terminal panes or read a specific pane's output.

```bash
openmax panes        # list all panes
openmax panes 1      # read pane 1
```

### `openmax agents`

List the effective agent registry (built-in + configured).

```bash
openmax agents
openmax agents --cwd /path/to/repo
```

### `openmax projects`

Track and route tasks to project directories.

```bash
openmax projects add ~/code/my-app
openmax projects list
openmax projects status
openmax projects remove my-app
```

## Multi-Task Mode

Pass multiple tasks to run them concurrently — each gets its own Lead Agent, its own set of agent panes, and its own isolated branch.

```bash
openmax run "Build the REST API" "Create React components" "Write integration tests"
```

- Each task runs independently with its own Lead Agent and terminal panes
- Tasks that fail don't block others — you get partial results
- Use `--project` to route tasks to different directories

## Session Resume

Resume a previous session to continue where you left off:

```bash
# Start a named session
openmax run "Build the frontend" --session-id my-frontend

# Later, resume it
openmax run "Continue the work" --session-id my-frontend --resume
```

## Supported Agents

| Agent | Command | Notes |
|-------|---------|-------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `claude` | Best for most coding tasks |
| [Codex](https://github.com/openai/codex) | `codex` | OpenAI Codex CLI |
| [OpenCode](https://github.com/opencode-ai/opencode) | `opencode` | OpenCode CLI |

All agents run interactively in their own pane. You can click into any pane and type to intervene at any time. The lead agent also monitors and sends corrections automatically.

### Agent Selection

The lead agent automatically picks the best agent for each sub-task (`claude-code` is the default). Use `--agents` to restrict and prioritize:

```bash
# Prefer Codex, fall back to Claude Code
openmax run "Build the API" --agents codex,claude-code

# Only use Claude Code
openmax run "Refactor auth module" --agents claude-code
```

The **first agent in the list is the preferred default**. If the lead agent tries to use an agent not in the list, it falls back to the first one.

### Custom Agent Config

openMax merges agents from these locations (later sources override earlier ones):

1. Built-in agents
2. `~/.config/openmax/agents.toml`
3. `<workspace>/.openmax/agents.toml`
4. `OPENMAX_AGENTS_FILE=/path/to/agents.toml`

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

Placeholders: `{cwd}` / `{prompt}` (raw), `{cwd_sh}` / `{prompt_sh}` (shell-escaped).

## Project Archetypes

openMax recognizes common project types and applies the right decomposition strategy automatically:

| Archetype | Detected when | Decomposition |
|-----------|--------------|---------------|
| **Web App** | "web app", "full-stack", "SPA" | schema > API + frontend (parallel) > auth > tests |
| **CLI Tool** | "CLI", "command-line", "terminal tool" | arg parsing > core logic > output formatting > tests |
| **API Service** | "API", "REST", "microservice" | endpoints > middleware + auth (parallel) > models > tests |
| **Library** | "library", "package", "SDK" | core module > public API > docs + tests > packaging |
| **Refactor** | "refactor", "migrate", "restructure" | analysis > transform > update callers > tests |

Custom archetypes: add `.openmax/archetypes/*.yaml` to your project:

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

## How It Works

The lead agent has no direct access to files or code. It orchestrates entirely through MCP tools:

| Tool | Purpose |
|------|---------|
| `dispatch_agent` | Spawn an agent in a terminal pane |
| `read_pane_output` | Check what an agent is doing |
| `send_text_to_pane` | Send follow-up instructions |
| `list_managed_panes` | Get pane states |
| `mark_task_done` | Track sub-task progress |
| `report_completion` | Finalize and report results |

**Cleanup:** On exit (normal, Ctrl-C, or SIGTERM), all managed panes and branches are cleaned up automatically. Use `--keep-panes` to keep them open, or `openmax clean` to remove leftovers from interrupted runs.

## Best Practice: Let Your Agent Call openMax

The most powerful way to use openMax is to **let your AI agent delegate work to it**. Instead of running openMax manually, instruct your agent (Claude Code, Cursor, etc.) to spawn openMax as a background process:

```bash
# In your agent's prompt or CLAUDE.md:
"When you need to parallelize work across multiple files or modules,
 run openmax in the background and continue with other tasks."
```

Example — your agent is building a full-stack app:

```bash
# Agent spawns openMax in the background, then continues its own work
openmax run "Build React dashboard components" --cwd ./frontend &

# Meanwhile, the agent works on the backend itself
# ...

# Later, check the results
openmax panes
```

This turns a single agent into a **team of agents** — your primary agent handles the high-level architecture while openMax manages the parallel sub-tasks.

## License

MIT
