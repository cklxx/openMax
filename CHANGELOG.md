# Changelog

## 0.6.5

- **Fix**: Verification pane now runs in project cwd — previously launched without cwd, causing import errors and wrong-directory lint failures

## 0.6.4

- **Fix**: Verification pane keeps alive 5s after command completes — prevents exit-before-capture race that caused all verifications to return `inconclusive`

## 0.6.3

- **Fix**: Verification pane exit race condition — re-read cached output for exit marker when pane dies before capture
- **Fix**: Verification returns `inconclusive` instead of `timeout` when pane exits without errors, preventing lead agent from misinterpreting results
- **Feat**: Verification result status table in prompt — mandatory action for each status (pass/fail/inconclusive)
- **Feat**: Prompt enforces "Never rationalize away a non-pass result" to prevent lead agent from skipping debug cycles
- **Perf**: "Max 1 sentence between tool calls" (was 2) to reduce narration overhead and turn count

## 0.6.2

- **Fix**: Pre-existing TUI bridge test failure (stale assertion from copy-paste)
- **Perf**: Resume context budget halved (12K→6K chars), activity filtered to key events only
- **Perf**: Project snapshot minimal mode on resume — skips directory tree and tooling detection
- **Perf**: Clean repos show one-line `Branch: main (clean)` instead of full snapshot
- **Perf**: Sub-agent prompt injection compressed: checkpoint protocol 22→3 lines, identity block 13→7 lines, file protocol 5→1 line
- **Perf**: Formatting module data-driven rewrite (220→147 lines) — format spec table replaces 133-line if-else chain
- **Perf**: Directory tree capped at 12 entries (was 30)

## 0.6.1

- **Perf**: Lead agent prompt compressed 25% (263→196 lines) — deduplicated rules, unified agent types/roles, removed contradictions
- **Perf**: MCP tool descriptions shortened ~20% — less token overhead per turn
- **Fix**: Registered `find_files`, `grep_files`, `read_file` into lead agent ALL_TOOLS (were implemented but never exposed)
- **Feat**: Lead agent can now do lightweight file exploration directly instead of dispatching research agents for simple queries
- **Feat**: New monitor signals: "Output but no commit" and "Unresponsive >5min" for better agent lifecycle tracking
- **Feat**: Explicit decision authority framework — reversible decisions made immediately, irreversible escalated to user
- **Refactor**: Extracted `_error_context.py` (66 lines) and `_branch.py` (139 lines) from oversized modules

## 0.6.0

- **Breaking**: CLI command semantic restructure — 17 commands consolidated to 12 visible commands with grouped help output
  - `runs` → `sessions`
  - `list-agents` → `agents`
  - `read-pane` merged into `panes` (use `openmax panes 5` to read pane 5)
  - `tail` + `replay` merged into `log` (`--follow`/`-f` = tail, default = replay)
  - `validate-config` merged into `doctor` (always runs config validation)
  - `install-skill` merged into `setup --skills` / `setup --skills-global`
  - `msg` hidden from `--help` (still works for IPC)
- **Feature**: Grouped `--help` output — commands organized into Run, Sessions, Environment, and Setup sections

## 0.5.53

- **Perf**: Lazy-load `lead_agent`/`claude_agent_sdk` — `openmax --help` drops from ~1.2s to ~0.4s
- **Feat**: `openmax "task"` now routes directly to `run` (no subcommand needed)
- **Feat**: `run` auto-detects and attaches existing terminal panes (merged from `manage`)
- **Breaking**: Removed `manage` command — use `run` instead (pane reuse is automatic)

## 0.5.52

- **Fix**: `TuiDashboard.stop()` crash when app already exited or failed to start — guard `call_from_thread` with `RuntimeError` catch

## 0.5.51

- **Fix**: TUI crash on startup — suppress `signal.signal` for entire Textual app lifecycle in background thread (covers `_build_driver`, `start_application_mode`, and `SIGWINCH` handler registration)

## 0.5.50

- **Fix**: TUI crash on startup (incomplete) — only patched `_build_driver`, missed `start_application_mode` signal calls

## 0.5.49

- **Simplify**: Version-gated TUI refresh — skip `deepcopy` when bridge state hasn't changed, reducing GC pressure
- **Simplify**: Deduplicate status icons (consolidated from 3 copies to 1 shared `STATUS_SYMBOLS`)
- **Simplify**: Single-pass task counting in status bar, reuse `_format_tokens` from dashboard module
- **Simplify**: Remove private helpers from widget package `__all__`

## 0.5.48

- **Feature**: Textual TUI Dashboard — interactive terminal UI with task list, DAG dependency graph, live log viewer, keyboard navigation, and progress bar
  - `DashboardProtocol` + `create_dashboard()` factory for pluggable dashboard backends
  - Pure-function DAG renderer (`tui/dag.py`) for visualizing parallel_groups
  - Thread-safe `DashboardBridge` + `DashboardState` for cross-thread communication
  - `OpenMaxApp` with `TaskListWidget`, `LogViewerWidget`, `StatusBarWidget`, `DagViewWidget`
  - `ConsoleProxy` for capturing console output into TUI log panel
  - `--no-tui` CLI flag to fall back to classic Rich status bar
- **Fix**: codex `auth.json` crash — `last_refresh` integer timestamp caused immediate exit (converted to RFC 3339 string)

## 0.5.47

- **Feature**: `execute_with_codex` MCP tool — Claude plans, Codex implements; synchronous `codex exec` call with output capture, timeout, and approval mode control
- **Feature**: `/codex` skill — plan-then-execute workflow for Claude Code users (`openmax install-skill` now installs both `/openmax` and `/codex`)
- **Refactor**: `skills.py` supports multiple skill files; `install-skill` CLI installs all skills at once

## 0.5.46

- **Feature**: sub-agent prompt restructure — identity block (task name + session_id) now placed at the top of agent prompts so agents know who they are even if prompt is truncated
- **Feature**: explicit MCP callback examples with pre-filled `session_id` in both prompt and CLAUDE.md — agents no longer have to guess the correct parameters
- **Fix**: `report_progress` no longer hard-fails when `session_id` is missing — returns `ok: true` with a warning instead, preventing agent workflow interruption
- **Cleanup**: condensed File Protocol section — removed redundant brief reference and verbose report template (already in CLAUDE.md)

## 0.5.45

- **Feature**: auto agent selection — `agent_type` is now optional in `dispatch_agent`; system auto-infers from `role` (reviewer/challenger/debugger→claude-code, writer→codex) when both agents are available
- **Feature**: `submit_plan` subtasks now accept optional `agent_type` field for pre-assigning agents in the plan, displayed during plan confirmation

## 0.5.44

- **Feature**: agent selection strategy — when both `claude-code` and `codex` are available, lead agent now prefers claude-code for research/analysis and codex for implementation/execution, with strategy table in system prompt and dynamic hint injection

## 0.5.43

- **Fix**: suppress noisy warning when stats file doesn't exist on first run — silently return defaults instead of logging an error

## 0.5.42

- **Fix**: MCP progress/done tools now accept explicit `session_id` parameters and keep `OPENMAX_SESSION_ID` as a fallback — sub-agents no longer depend entirely on env propagation through nested CLI/MCP process chains
- **Feature**: two-layer dashboard display — default mode replaces pane_id with activity column showing last pane output; `--verbose`/`-v` flag on `run`/`manage`/`loop` adds pane_id and dispatch prompt detail rows
- **Feature**: `openmax inspect` subtask table now includes Elapsed and Notes columns

## 0.5.41

- **Feature**: auto-detect project lint/test tooling — new `project_tools.py` scans for config files (pyproject.toml, package.json, go.mod, Cargo.toml, .eslintrc, biome.json, etc.) and injects the correct lint/test commands into the Project State block; supports Python, JavaScript/TypeScript, Go, and Rust
- **Feature**: lead agent prompt now uses detected tooling for `run_verification` instead of hardcoded `ruff`/`pytest` commands

## 0.5.40

- **Fix**: git merge race condition — added `anyio.Lock` to serialize all state-modifying git operations (checkout, merge, branch create/delete, worktree add/remove), preventing repo corruption when multiple agents finish concurrently
- **Fix**: blocking `subprocess.run` calls in async context — git operations in `merge_agent_branch` and `_setup_branch_isolation` now run in worker threads via `anyio.to_thread.run_sync`, unblocking the event loop during git I/O

## 0.5.39

- **Fix**: acceleration ratio calculation read `depends_on` but `submit_plan` stores `dependencies` — critical path was always treating all tasks as independent, inflating the ratio
- **Chore**: cleaned up stale task index — 21 done, 4 removed (memory system), 3 pending (all P3)

## 0.5.38

- **Fix**: no-op merge skipping — branches with 0 new commits now skip `git merge` entirely and clean up silently, instead of creating pointless merge commits
- **Fix**: decoupled merge from `mark_task_done` — removed auto-merge; the lead agent now explicitly calls `merge_agent_branch` after marking tasks done, giving it control over conflict resolution via sub-agents
- **Fix**: command panes (`run_command`) now auto-mark as done when the pane exits, instead of staying stuck in RUNNING state

## 0.5.37

- **Refactor**: removed workspace memory system entirely — lessons, run summaries, agent rankings, predictive context, and all related CLI commands (`memories`, `recommend-agents`, `recommendation-eval`) and lead agent tools (`remember_learning`, `get_agent_recommendations`) deleted; ~4100 lines removed

## 0.5.36

- **Feature**: add Ghostty terminal backend — GPU-accelerated macOS terminal support via AppleScript API; auto-detected between kaku and tmux in priority order `kaku > ghostty > tmux`
- **Feature**: `openmax doctor` now checks for Ghostty alongside kaku and tmux
- **Feature**: `--pane-backend ghostty` option available on `run`, `manage`, and `loop` commands

## 0.5.35

- **Fix**: `_launch_pane` now falls back to opening a new terminal window when kaku reports `Error: No space for split!` — previously `dispatch_agent` and `run_command` would fail with a hard error when the current window was full; now they seamlessly overflow into a new window

## 0.5.34

- **Fix**: `merge_agent_branch` now detects empty branches (no new commits) and logs a `[yellow]!` warning with `[0 commits - no-op]` in the output — surfaces the hash bug where agents committed to main instead of their worktree branch
- **Feature**: merge-on-done workflow — lead agent now calls `merge_agent_branch` immediately when each agent reports `done`, instead of batching all merges at the end; Finish section reduced to verify + check_conflicts + report

## 0.5.33

- **Feature**: tmux backend now applies `select-layout tiled` after every `split_pane` — all managed panes auto-tile into an even grid whenever a new sub-agent is dispatched
- **Fix**: lower `claude-agent-sdk` lower bound to `>=0.1.48` (latest published version); CI was failing with "no matching distribution" for `>=0.1.49`
- **Fix**: add `pytest-asyncio>=0.23` as dev dependency and set `asyncio_mode = "auto"` — CI `test_planning.py` async tests were failing with "async def functions are not natively supported"

## 0.5.32

- **Fix**: final "X done" summary now counts `plan.subtasks` with `DONE` status instead of `pane_mgr.summary()['done']` — pane states are never refreshed at session end so the old count always showed 0
- **Fix**: mailbox notify instruction moved into File Protocol section (alongside report-write step) so sub-agents cannot miss it; removed from optional context block
- **Fix**: `max_turns` default changed from 50 to unlimited (`None`) for `run` and `manage` commands — `loop` keeps 50 per iteration

## 0.5.31

- **Fix**: `dispatch_agent` now injects `OPENMAX_SESSION_ID` into the pane's env vars, so sub-agents can run `openmax msg --session "$OPENMAX_SESSION_ID"` without parsing the prompt text

## 0.5.30

- **Fix**: `SessionMailbox._serve` loop was exiting immediately after the first 1s accept timeout because `socket.timeout` is a subclass of `OSError` — changed to `except TimeoutError: continue` so the server stays alive and polls `_stop` each second

## 0.5.29

- **Feature**: Session Mailbox — sub-agents push `done`/`progress`/`question`/`blocked`/`decision` messages to the lead agent via Unix socket, eliminating polling latency
- **Feature**: `wait_for_agent_message` MCP tool — replaces `wait` as the primary monitoring primitive; returns immediately on message arrival; auto-synthesizes `done` for panes that exit without messaging
- **Feature**: `openmax msg` CLI — sub-agents call `openmax msg --session <id> '<json>'` to notify the lead agent; reads `OPENMAX_SESSION_ID` env var
- **Feature**: `openmax tail` / `openmax replay` CLI — stream live messages or replay completed session message log
- **Improve**: sub-agent prompts include `OPENMAX_SESSION_ID` and mailbox instructions via `_build_subagent_context`
- **Improve**: lead agent prompt — new Mailbox section with action table before Monitor loop

## 0.5.28

- **Fix**: dashboard UI overlap — `print_agent_text` now routes through `console.print(Text.from_ansi(...))` instead of writing directly to `console.file`, so Rich's `Live` widget correctly manages cursor position when the status bar is active

## 0.5.27

- **Feature**: acceleration ratio — compute `wall_clock / critical_path` to show parallelization benefit; displayed in scorecard and report completion panel
- **Feature**: orchestration overhead breakdown — classify session time into agent/dispatch/monitor/merge/other with percentage display
- **Improve**: dashboard — bold/dim/strike row styles per subtask status, phase duration rollup, and bold green "ALL DONE" banner when all tasks complete

## 0.5.26

- **Improve**: lead agent prompt — dispatch briefs require named failure modes and four shadow paths (happy/nil/empty/error); plan phase adds focus-as-subtraction to counter over-splitting; ask_user options include completeness score (X/10) and dual effort scale (human vs agents)

## 0.5.25

- **Improve**: lead agent prompt — add strategic thinking patterns: completeness principle (prefer thoroughness over shortcuts), pre-mortem step in planning, NOT-in-scope discipline, edge case checklist in dispatch briefs, zero silent failures in monitoring, reversibility gate for escalation decisions, structured ask_user format

## 0.5.23

- **Feature**: cost forecasting — `dispatch_agent` estimates token usage and USD cost before dispatch; estimate included in response payload and session events
- **Feature**: budget hard stop — `read_pane_output` returns `"action": "stop_agent"` when a subtask exceeds its token budget hard limit
- **Feature**: adversarial agent roles — new `role` parameter on `dispatch_agent` (`writer`/`reviewer`/`challenger`/`debugger`); non-writer roles inject behavioral instructions (e.g. reviewer finds bugs without committing)
- **Feature**: lead agent prompt documents role workflows — dispatch writer → reviewer → synthesize feedback

## 0.5.22

- **Fix**: spinner now runs at 20 fps with `dots2` style — smoother animation, no more choppy frames
- **Fix**: markdown output spacing — collapse 3+ consecutive newlines to 2; no more double blank lines between table rows and paragraphs

## 0.5.21

- **Feature**: interactive plan confirmation — `submit_plan` now presents the proposed plan to the user and waits for approval before dispatching agents; user can approve, or provide feedback to trigger a revision loop
- **Feature**: `--no-confirm` flag on `run` and `manage` commands to skip plan confirmation (for automation/scripting)

## 0.5.20

- **Fix**: no more blank gap after "connecting" spinner — `mark_connected` now transitions to "thinking" instead of stopping; spinner only clears when first tool event fires, so there's always something visible while the model is generating
- **Improve**: spinner lifecycle: `starting up` → `thinking` → disappears on first tool use

## 0.5.19

- **Improve**: `openmax manage TASK` now snapshots the last 30 lines of every attached pane's output before starting the lead agent — agent receives full context of what each CLI is currently doing without needing to call `read_pane_output` for each one

## 0.5.18

- **Improve**: lead-agent text output now rendered as markdown via Rich `Markdown` renderer — headers, tables, bold, code blocks all display correctly instead of raw `│`-prefixed plain text

## 0.5.17

- **Improve**: ANSI stripping for stuck detection — `strip_terminal_noise` removes ANSI escapes, progress bar chars, and spinner symbols before hashing; prevents false negatives where cosmetic output changes mask a stuck agent
- **Improve**: Claude Code ready patterns expanded — added `"for help"`, `"Claude Code"`, `"Type your"` to handle newer CLI versions
- **Improve**: `read_pane_output` returns `cached: true` when output is from a dead pane's last capture, so lead agent can distinguish live vs stale output
- **Improve**: `dispatch_agent` returns `ready_timeout: true` when CLI did not show ready signal before prompt was sent, enabling lead agent to monitor more aggressively
- **Improve**: lead agent prompt updated with new monitoring signals: cached output, ready timeout, and clarified stuck detection
- **Refactor**: shared test helpers extracted to `tests/conftest.py`

## 0.5.16

- **Feature**: `openmax manage [TASK]` — new command to discover and manage all existing terminal panes/windows
  - Without TASK: displays a rich table of all running panes grouped by window (title, CWD, active marker)
  - With TASK: attaches all discovered panes as external managed panes, injects their context into the lead agent prompt, and runs the orchestration loop so the agent can read/send to pre-existing sessions alongside dispatching new ones
  - External panes are never killed on cleanup — `cleanup_all` skips panes/windows marked `external=True`
- **`PaneManager.attach_pane(pane_info, purpose)`** — register a pre-existing `PaneInfo` into management without launching anything; uses `external=True` marker on both `ManagedPane` and `ManagedWindow`

## 0.5.15

- **Test**: `tests/test_stability.py` — 12 stability/recoverability tests: agent crash detection, dead-pane cached output, stuck agent detection, send-to-dead-pane error, dispatch backend failure, mark-done on dead pane, resume stale task reset, verification pass/fail with dispatch_hint, all-panes summary, concurrent dispatch, and task name deduplication

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
