# Changelog

## 0.5.14

- **Refactor**: eliminate redundant `total` variable in `build_loop_context` — use `n_total` computed once before the conditional

## 0.5.13

- **Fix**: `atexit` handler accumulation in `openmax loop` — each iteration registered a new handler without deregistering; now calls `atexit.unregister` after cleanup, preventing N handlers at process exit on long loops
- **Fix**: `build_loop_context` now caps at last 10 iterations with a truncation notice; prevents unbounded prompt growth after 50+ iterations
- **Fix**: `lead_agent.md` Finish section — added explicit step 0 requiring `mark_task_done` for all completed subtasks before `report_completion`, with explanation of consequences

## 0.5.12

- **E2E tests**: `tests/test_e2e.py` — two real-API tests guarded by `OPENMAX_E2E=1`; scripted `task-agent` writes proper `.openmax/reports/` files so the full orchestration loop (dispatch → read → mark done → report) is exercised end-to-end
- **Fix**: lead agent `mark_task_done` now required in Monitor section — was calling `report_completion` without first marking subtasks done, leaving them in RUNNING state and breaking loop tape done-task tracking; added explicit `mark_task_done` rules to Monitor table and conditional trigger table in `lead_agent.md`

## 0.5.11

- **`openmax loop` tape context**: each iteration now injects a structured "Loop Context" block into the lead agent's prompt — lists all prior iterations with timestamps, subtask names, and completion %; lead agent explicitly told not to repeat completed work; inspired by bub's tape-based context design
- **`loop_context` param**: `run_lead_agent` / `_build_lead_prompt` accept an optional `loop_context` string injected before memory context
- **`LoopSessionStore`**: new `src/openmax/loop_session.py` — JSONL tape at `~/.openmax/sessions/loops/<id>.jsonl` records every iteration; survives restarts

## 0.5.10

- **Refactor**: split `lead_agent/tools.py` (2168-line God Object) into `tools/` package — `_dispatch`, `_planning`, `_shared`, `_verify`, `_report`, `_misc`, `_helpers` modules; no behavior change
- **Fix**: `lead_agent.md` system prompt — added `### Dispatch Failures` section; Lead now retries `dispatch_agent` once on failure and never bootstraps agents via `send_text_to_pane`
- **Fix**: session event log pruning — `lead.message` events trimmed to last 50 when count exceeds 100, preventing unbounded JSONL growth on long runs
- **Fix**: `KakuPaneBackend.spawn_window` / `split_pane` now retry up to 2× on `PaneBackendError` with 0.5s delay, reducing transient kaku CLI timeout failures

## 0.5.9

- **`openmax loop`**: new CLI command for continuous/infinite orchestration — runs `run_lead_agent` in an outer loop with a fresh `PaneManager` each iteration; memory accumulates across iterations; supports `--max-iterations`, `--delay`, `--agents`, `--pane-backend`, `--max-turns`; graceful Ctrl+C shows total iteration count

## 0.5.8

- **Shared Blackboard**: `update_shared_context` / `read_shared_context` tools let Lead write architectural decisions to `.openmax/shared/blackboard.md`; every dispatched sub-agent automatically receives relevant blackboard content in its brief
- **Checkpoint Pattern**: sub-agents can pause at decision forks by writing `.openmax/checkpoints/{task}.md`; Lead detects these via `check_checkpoints`, decides (or escalates to human), and sends the decision back via `resolve_checkpoint` → `send_text_to_pane`
- **Active monitoring loop**: Lead's prompt now includes `check_checkpoints` in every monitoring round; `ask_user` reserved for product/policy decisions only — technical choices are Lead's to make
- 4 new MCP tools: `update_shared_context`, `read_shared_context`, `check_checkpoints`, `resolve_checkpoint`

## 0.5.7

- openMax is now an installable skill for AI coding agents (Claude Code, Codex, OpenCode)
- `skills/openmax.md`: skill file that wraps `openmax run` as a `/openmax` slash command
- `openmax install-skill [--global]`: deploy the skill to current project or `~/.claude/commands/`
- `install_skills.sh`: shell script alternative for installation

## 0.5.6

- Add cross-agent skills system: 6 built-in skills (commit, release, test, lint, debug, research) in `skills/`
- Claude Code: skills auto-available as `/commit`, `/release`, `/test`, `/lint`, `/debug`, `/research` via `.claude/commands/` symlinks
- Codex / OpenCode: `skills.inject_skill(prompt, name)` API injects skill content into dispatch prompts
- `install_skills.sh`: one-command install to project or global `~/.claude/commands/`
- `openmax skills [name]` CLI command: list available skills or print a skill's body

## 0.5.5

- Simple tasks (single-file, clear scope) now skip research + plan and dispatch directly to claude-code — no unnecessary overhead

## 0.5.4

- Fix: lead agent now always runs `run_verification` (lint + test) after merging all branches — was transitioning to verify phase but silently skipping the actual checks

## 0.5.3

- Remove `clear_screen` — ANSI clear/scroll sequences caused terminal artifacts in Claude Code, tmux, and other environments

## 0.5.2

- Use scroll-to-top instead of erasing screen on startup — old content preserved in terminal scrollback

## 0.5.1

- Fix `openmax models` crashing when `ANTHROPIC_API_KEY` is not set (Claude Code uses OAuth, not API key) — now falls back to a built-in list of known models

## 0.5.0

- `openmax models`: interactive model selector — fetches available models from Anthropic API, user picks by number or pastes ID, saves to `~/.openmax/config.json`
- `openmax run` reads saved model from config as default; `--model` still overrides per-run

## 0.4.9

- Remove sub-agent model selection: dispatch_agent no longer has a `model` param, adapters no longer accept `--model` CLI flags; each CLI tool manages its own model config
- Remove `--sub-model` CLI flag, `model_list.py`, and `anthropic` dependency

## 0.4.8

- Sub-agent model selection: pass `model` in `dispatch_agent` to choose a specific Claude model per task; `--sub-model` CLI flag sets the session default; available models fetched from Anthropic API and injected into lead agent context
- Fix phase transition state machine: `research → plan → implement → verify` now works correctly (code previously allowed `research → implement` directly, contradicting the system prompt)
- Tighten lead agent research guidance: targeted research prompt template, explicit skip conditions (single-file, user-provided paths, visible project structure)
- Sync `wait` tool description with system prompt timing (30-45s for complex tasks)

## 0.4.7

- Fix merge conflict detection reading stdout instead of stderr — git writes "Merge conflict in X" to stderr
- On conflict, capture full `git diff branch...integration` and include it plus a `resolve_hint` in the response so the lead agent can write a semantically-informed resolution prompt
- Update lead agent prompt: on conflict, use the diff to tell the resolution agent exactly what each side changed and what the correct resolution should be

## 0.4.6

- Fix `submit_plan` and `transition_phase` tools sending `list` params as `"string"` to Claude — use proper JSON Schema (`{"type": "array"}`) so Claude constructs calls correctly

## 0.4.5

- Fix clear_screen using Rich console.print which corrupted ANSI escape sequences — use raw stdout instead

## 0.4.4

- Fix sub-agents repeatedly running `cd` in worktrees: remove redundant `--add-dir`, inject explicit working directory into prompt context, skip pane reuse across different worktrees
- Fix clear_screen using Rich console.print which corrupted ANSI escape sequences — use raw stdout instead

## 0.4.3

- Save full pane output to .openmax/logs/ (no line limit) as permanent audit trail
- Always persist briefs and reports to main cwd so they survive worktree cleanup
- Two-layer .gitignore (nested + root append) to avoid merge conflicts

## 0.4.2

- Fix `PaneManager.add_pane()` missing `title` parameter — unblocks all dispatch_agent and run_command calls

## 0.4.1

- Vite-style clear screen on CLI startup for a clean, distraction-free experience
- Remove redundant task text echo (user already typed it)
- Inject report instructions into worktree CLAUDE.md for more reliable sub-agent reporting
- Fallback: synthesize report from pane output when agent doesn't write one

## 0.4.0

- Add file-based context exchange protocol between lead agent and sub-agents
- Lead agent writes task briefs to `.openmax/briefs/{task_name}.md` on dispatch
- Sub-agents instructed to write completion reports to `.openmax/reports/{task_name}.md`
- New `read_task_report` MCP tool for lead agent to read structured reports
- `read_pane_output` auto-includes report content when pane has exited
- `mark_task_done` auto-reads report file into subtask completion notes
- `.openmax/` directory auto-gitignored to prevent task files from being committed

## 0.3.9

- Fix dispatch/run/verify tools crashing on pane backend errors — now returns structured error to lead agent with remediation hint
- Record `tool.dispatch_agent.failed` events so failures are visible in session history
- Fix branch isolation failing on retry when branch already exists — reuse existing branch+worktree instead
- Preserve reused branches on worktree creation failure (don't delete prior agent commits)

## 0.3.8

- Fix module layering: move LeadAgentRuntime from persistence to orchestration layer
- Decouple shared console from dashboard into dedicated output.py
- Extract shared path/time utilities into _paths.py
- Consolidate duplicate serialize functions across module boundaries

## 0.3.7

- Systematic code quality cleanup: -248 lines, zero behavior changes
- Remove unused interactive mode (inspect/send/summary/quit loop)
- Extract shared helpers in tools.py: `_tool_response`, `_launch_pane`, `_merge_and_handle_conflicts`
- Refactor `reconstruct_plan()` from 225-line if-chain to event dispatch dict
- Merge duplicate coercion functions, extract eviction score helpers
- Simplify pane_manager env-passing and extract helpers
- Extract status style constants and table factory in CLI

## 0.3.5

- CLAUDE.md: add code style section (15-line max, composition, transform pipelines)
- CLAUDE.md: replace hardcoded project structure with discovery command
- Lead agent prompt: 3-layer verification workflow (agent self-verify → lead check → debug agent)
- Lead agent prompt: tighter dispatch checklist, monitor signal table

## 0.3.2

- Lead agent acts as team manager, never explores code itself
- Transient API errors handled with retry and proper error display
- Tmux backend auto-creates session, no manual setup needed

## 0.3.0

- Tmux backend for cross-platform terminal pane support (no longer Kaku-only)
- Enriched lead agent and sub-agent context for better task awareness

## 0.2.1

- Usage tracking and provider status with live quota
- Total summary added to status output

## 0.2.0

- Interactive post-run mode and `run_command` tool
- Dashboard and prompt improvements
- Lead agent and memory system split into packages

### Tools

- `submit_plan`: structured task decomposition (1.1)
- `run_verification`: lint/test verification tool (5.2)
- `transition_phase`: explicit phase gating (8.1)
- `check_conflicts`: git conflict detection
- Stuck detection in `read_pane_output`
- Pane exit detection and retry tracking
- `ask_user` with choices support

### Memory

- Eviction scoring with age+relevance and capacity limit (7.1)
- Auto-inject memory context into dispatch prompts

### Reliability

- Branch isolation and auto-merge (11.1)
- Context compression protocol (11.2)
- Failure auto-retry (10.1)
- Robust window management with retry and dead-pane recovery
- Kill orphan panes during cleanup, re-list after straggler kill

### Dashboard

- Subtask table, phase dividers, progress bar
- Elapsed time tracking for subtasks

## 0.1.9

### Predictive Memory System

Three optimisations inspired by the predictive memory paradigm — shifting from
passive "query → retrieve" to proactive "predict → pre-stage":

- **Session-end prediction**: on run completion, automatically predict likely
  follow-up queries (e.g. "write tests" after a code task) and store predictions
  alongside the run summary for future context pre-staging
- **Query-distribution-weighted priority**: track per-workspace task-category
  distribution (code / testing / debugging / refactor / architecture / docs) and
  boost memory entries matching high-frequency categories
- **Dual-buffer context**: `build_context` splits into an *active buffer*
  (keyword-matched, ~67% budget) and a *predictive buffer* (prediction-matched +
  distribution-boosted, ~33% budget), solving the orthogonal-causal retrieval
  blind spot where the query and the needed memory share no keywords

### Auth & Setup

- New `openmax setup` command: runs `claude setup-token` for long-lived auth
- Dedicated `auth.py` module extracts auth detection from doctor/adapters
- `openmax setup --status` shows current auth state
- Claude Code adapter simplified — no longer reads OAuth token inline

### Dashboard & UX

- New `dashboard.py`: live run-progress display with phase tracking
- Lead agent prompt refined for clearer three-phase workflow
- Session runtime improvements for resume context handling

## 0.1.8

- `openmax doctor` command: checks Python, Kaku, agent CLIs, and auth status
- startup_delay replaced with ready signal polling — prompt no longer silently dropped on slow CLIs
- Smart pane output: error/traceback lines auto-surfaced from beyond the 150-line window
- `openmax validate-config [--cwd]`: validate agent TOML config and show per-agent status
- Auto-resume detection: `openmax run` prompts to resume unfinished session for same task+cwd
- `openmax list-agents --verbose`: show command template for each agent
- Session resume: stale running subtasks reset to pending when their panes are gone

## 0.1.4

- Agent window resized to 50% of screen (was 68%)
- Review & Verify phase added to lead agent workflow
- System prompt extracted to markdown, CEO-mindset rewrite
- `wait` tool for throttled monitoring
- Fix window resize targeting wrong window
- Fix `datetime.UTC` for Python 3.10 compat
- CLAUDE.md and AGENTS.md added
- GitHub Pages deployment for landing page

## 0.1.3

- Reliable cleanup: verify + retry killing panes, atexit safety net
- Auto-detect Kaku: prompt `brew install --cask kaku` if missing
- Agent window auto-resizes to 68% of screen
- `--keep-panes` option to preserve panes after session
- Banner image and README refresh

## 0.1.1

- All agents share one Kaku window with smart grid layout
- `PaneManager` tracks window-pane topology
- `dispatch_agent` uses `create_window` / `add_pane` pattern

## 0.1.0

- Initial release
- Lead agent via claude-agent-sdk with 6 custom MCP tools
- Interactive agent adapters: Claude Code, Codex, OpenCode, Generic
- Kaku terminal pane management
- Signal handling for clean shutdown
