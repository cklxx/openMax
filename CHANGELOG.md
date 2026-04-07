# Changelog

## 0.9.45

- **Fix**: Tmux session recovery — `spawn_window` now re-creates the `openmax` session if it was externally killed (e.g., all agent panes exited), preventing "can't find session" errors during dispatch

## 0.9.44

- **Improve**: Upgrade multi-task decomposition model from Haiku to Sonnet 4.6 for better recognition accuracy

## 0.9.43

- **Refactor**: Remove fixed-format multi-task recognition (numbered lists, `---` separators, `##` headings) — only LLM-based decomposition remains, preventing false positives when user prompts contain those patterns but aren't actually independent tasks

## 0.9.42

- **Feat**: Interactive mode (`-i` / `--interactive`) — human-in-the-loop feedback between iterations. After each run completes, review results and provide feedback to guide the next iteration. Type `q` or press Enter to exit.

## 0.9.41

- **Fix**: Prevent JSON buffer overflow crash in lead agent SDK communication — 5-layer defense: increased SDK buffer to 8MB, added 500KB safety cap on tool responses, capped mailbox message strings to 4KB, limited monitor results to 50 entries, added 30KB char limit to `_extract_smart_output`, and added buffer overflow to transient retry patterns
- **Fix**: `read_file_tool` — cap individual lines to 1000 chars to prevent minified-file blowup

## 0.9.40

- **Feat**: Phase-based dependency dispatch — plans with phased dependencies (e.g., Phase 1 must complete before Phase 2 starts) now correctly dispatch dependent tasks automatically when their prerequisites finish
- **Fix**: All subtasks registered upfront with PENDING status so monitoring loop correctly tracks the full task graph
- **Fix**: `_all_tasks_done()` now checks for terminal states (DONE/ERROR) instead of just "not RUNNING", preventing false completion when PENDING deps exist

## 0.9.39

- **Fix**: `provider_usage` — guard against null usage field (not just missing key); use `or 0` so null token values don't propagate to `+=` arithmetic
- **Fix**: `usage` — same `or 0` fix in `usage_from_result` when token values are null
- **Fix**: `core` — replace `getattr(dict, ...)` with `dict.get()` — `getattr` on a dict looks up attributes, not keys, silently returning 0 even when data was present
- **Tests**: Regression tests for null usage field, null token values, and missing token fields

## 0.9.38

- **Fix**: Double-processing — harness `_mark_and_merge` was redundant with `_auto_mark_and_merge` in mailbox handler; removed duplicate mark/merge calls
- **Fix**: Force-mark safety — agents exiting without MCP done callback now get force-marked as done when pane death is confirmed, preventing subsequent phases from hanging
- **Improvement**: `run_harness_workflow` refactored to 12-line body with `_HarnessState` dataclass, `_run_harness_round`, and `_emit_harness_complete` helpers

## 0.9.37

- **Fix**: Harness worktree isolation — planner/evaluator write `.openmax/` files in worktrees (gitignored), added `_persist_from_worktree` to copy specs and evaluations back to main cwd
- **Fix**: Planner merge skip — planner doesn't commit code, so merge was a no-op; now correctly skips merge
- **Fix**: Evaluator role template — removed unpopulated `{task_name}`/`{round}` placeholders, added "be skeptical" instruction per Anthropic checklist
- **Fix**: Evaluation parser robustness — now handles `###` headings, `1.` numbered prefixes, case-insensitive and partial dimension matching
- **Fix**: Pivot threshold too tight — widened from 0.5 to 1.0 weighted average regression
- **Improvement**: Archetype-aware evaluation dimensions — frontend projects use design/originality-weighted rubric; backend/API/CLI use product-depth/functionality-weighted rubric
- **Improvement**: Empty spec/score warnings — harness prints warnings when planner or evaluator produce empty output
- **Tests**: 9 new tests for fuzzy heading matching, numbered headings, dual dimension sets

## 0.9.36

- **Feature**: Harness mode (`--harness`) — Planner → Generator ↔ Evaluator quality loop inspired by Anthropic Labs harness checklist
- **Feature**: Planner role — dispatches agent to produce bold product spec (user stories, data model, design language) without implementation details
- **Feature**: Evaluator role — independent quality assessment via live app browser interaction with calibrated scoring dimensions (design quality 35%, originality 30%, craftsmanship 20%, functionality 15%)
- **Feature**: Threshold-driven iteration — evaluator scores drive accept/refine/pivot decisions, up to 5 rounds with quality peak detection
- **Feature**: Sprint contracts — per-round acceptance criteria written to `.openmax/contracts/`, evaluation reports to `.openmax/evaluations/`
- **Feature**: Harness session events — `harness.planner_done`, `harness.evaluation`, `harness.decision`, `harness.complete` for metrics tracking
- **Improvement**: File helpers for specs, contracts, and evaluations in `task_file.py`
- **Tests**: 31 new tests covering harness scoring, evaluation parsing, decision logic, prompt builders, file helpers, and CLI flag

## 0.9.35

- **Fix**: `execute_with_codex` MCP tool — replaced invalid `-a` flag with `--full-auto` to match codex CLI v0.116.0 interface; `suggest` mode omits the flag for interactive approval
- **Improvement**: Tests now assert exact command construction, catching CLI argument mismatches

## 0.9.34

- **Fix**: Stale session resume — interrupted sessions (Ctrl+C / SIGTERM) left `active` status because `SystemExit` bypasses `except Exception`; `finally` block now marks them `aborted`
- **Feature**: Employee API (`GET /api/employees`) — exposes team roster to dashboard
- **Feature**: Sidebar Team section — shows onboarded employees with avatar initials, role, specialty, and task count
- **Improvement**: Stats bar shows employee count alongside task metrics
- **Improvement**: Better empty state with icon, refined header with active task spinner, sticky bottom input bar
- **Fix**: Auto-abort stale sessions on startup — `expire_old_sessions()` marks `active` sessions older than 2h as `aborted`, clearing residual state without manual cleanup
- **Fix**: Session staleness guard — `_detect_resumable_session` ignores sessions older than 2h, preventing false resume prompts

## 0.9.33

- **Feature**: React + shadcn/ui dashboard — replaces vanilla JS with React component tree, zustand state management, and shadcn/ui design system (Geist font, Linear/Vercel aesthetic)
- **Improvement**: Reactive rendering via zustand signals — WebSocket events update store, only affected components re-render (no scroll/focus loss during activity log updates)
- **Improvement**: Collapsible task cards with subtask list, progress bar, and activity log (powered by shadcn Collapsible component)
- **Fix**: Remove unused asyncio.Lock from TaskQueue (asyncio single-thread model makes dict operations atomic between awaits)
- **Fix**: WebSocket handler exception leak — catch all exceptions in ws_hub.handle(), not just WebSocketDisconnect
- **Improvement**: Refactor task_runner oversized functions — `_run_single_task()` split from 51 to 15 lines, `run_tasks()` from 33 to 10 lines

## 0.9.32

- **Improvement**: Task input bar moved to floating bottom dock — sticky positioning keeps the input visible while scrolling, with subtle shadow and gradient fade
- **Fix**: Add asyncio.Lock to TaskQueue for thread-safe concurrent access from scheduler, HTTP routes, and progress bridge
- **Fix**: Replace deprecated `asyncio.get_event_loop()` with `get_running_loop()` across server module
- **Fix**: Track detached async tasks in scheduler for proper cancellation on shutdown
- **Fix**: ProgressBridge `call_soon_threadsafe` race condition — wrap in try/except for loop-closed edge case
- **Improvement**: Initialize module globals as Optional to avoid NameError before `create_app()`
- **Improvement**: Split `create_app()` to comply with 15-line function body rule
- **Tests**: 16 new scheduler integration tests — state transitions, slot cost accounting, progress bridge forwarding

## 0.9.31

- **Feature**: Serve mode auto-approve — `openmax serve` no longer prompts for plan confirmation in the CLI terminal; tasks execute autonomously
- **Feature**: Mailbox observer callback — `SessionMailbox.on_message` enables real-time progress forwarding from sub-agents to WebSocket dashboard
- **Feature**: Auto-retry on rate limit — serve mode automatically waits and retries on API rate limits instead of blocking on `input()`
- **Fix**: Progress bridge rewritten from polling to callback-based — sub-agent activity now properly appears in the dashboard instead of being consumed by the lead agent's internal monitoring loop
- **Improvement**: Dashboard UI polish — chevron toggle for expand/collapse, live duration counter on running tasks, better subtask layout with status badges, agent source highlighting in activity logs, pulse animation on running indicators

## 0.9.30

- **Feature**: Agent dispatch queue — `--max-agents N` limits concurrent sub-agents, excess tasks queued as pending and auto-dispatched when slots open
- **Feature**: Rate limit resilience — lead agent pauses and prompts user to confirm before retrying (up to 5 attempts)
- **Feature**: Sub-agent rate limit detection — `read_pane_output` flags `rate_limited: true` on exited panes with rate limit errors

## 0.9.28

- **Feature**: Rate limit resilience — lead agent pauses and prompts user to confirm before retrying (up to 5 attempts with Enter-to-retry)
- **Improvement**: Dashboard redesign — Light theme with low-saturation warm gray palette (#F7F7F5 base), sidebar navigation layout for wide screen support (1100-1600px adaptive), Linear/Notion-style minimal UI with desaturated accent colors
- **Feature**: Sub-agent rate limit detection — `read_pane_output` detects rate limit errors in exited panes and flags `rate_limited: true`
- **Improvement**: Lead agent prompt instructs `ask_user` before re-dispatching rate-limited sub-agents (one limit likely means all are limited)

## 0.9.27

- **Feature**: Stream-JSON execution mode (`claude-code-stream` adapter) — sub-agents run via `claude -p --output-format stream-json --verbose` with real-time event parsing
- **Feature**: Stream parser — extracts tool_use, text, and result events from stream-json output, updates dashboard activity lines in real-time
- **Feature**: Per-agent log files — each agent's stream written to `.openmax/logs/{task}.jsonl`
- **Feature**: Task management UI — status filter tabs (All/Queued/Running/Done), inline task editing, size selector (S/M/L), clear completed tasks
- **Improvement**: Softer Notion-style dark theme (#1E1F24 base) replacing pure black for reduced eye strain
- **Improvement**: HeadlessPaneBackend supports line-based stream capture with pluggable callbacks

## 0.9.25

- **Feature**: Real-time activity log per task — sub-agent progress, lifecycle events, and error messages stream into an expandable monospace log panel on each task card
- **Improvement**: Apple-style dashboard redesign — pure black base (#000), glass morphism cards with backdrop-filter blur, Apple HIG color system, 0.5px translucent borders, liquid glass shimmer highlights, system accent colors, Apple animation timing curves, reduced motion support

## 0.9.23

- **Improvement**: Dashboard UI redesign — card-style stats with colored icons, gradient progress bars, running glow effect, animated counters, polished input area with focus glow, responsive layout

## 0.9.22

- **Feature**: Service mode — `openmax serve` starts a persistent HTTP server with web dashboard for task queue management
- **Feature**: Web dashboard — real-time task progress visualization via WebSocket (queue/running/done states, subtask progress bars, priority controls)
- **Feature**: Task queue — filesystem-backed priority queue with submit/cancel/reprioritize from browser UI
- **Feature**: LLM task sizing — Haiku estimates task size (small/medium/large) for intelligent scheduling, user can override
- **Feature**: Smart scheduler — small tasks run direct, medium/large tasks go through lead agent, slot-based concurrency control

## 0.9.21

- **Fix**: LLM task decomposition uses `claude -p` CLI instead of Anthropic SDK, supporting OAuth auth (not just API key)
- **Fix**: Lower LLM decomposition threshold from 200 to 80 chars for CJK text
- **Fix**: Handle markdown code fences in LLM decomposition response

## 0.9.20

- **Feature**: Batch task mode — single prompt/file containing multiple tasks is auto-decomposed (numbered lists, `---` separators, `## ` headings, or LLM fallback via haiku), confirmed with user, and executed through a single lead agent that dispatches all sub-agents in parallel
- **Feature**: LLM-powered task decomposition — unstructured natural language prompts are split into independent tasks via Claude Haiku when structural patterns aren't found
- **Improvement**: Max concurrent agents raised from 6 to 30 in lead agent prompt
- **Improvement**: Mailbox socket backlog increased from 16 to 64 for high-concurrency scenarios

## 0.9.19

- **Fix**: Recognize `ANTHROPIC_AUTH_TOKEN` for auth detection and settings forwarding

## 0.9.18

- **Feature**: Persistent employee system — sub-agents now have durable identities stored in `~/.config/openmax/employees/` that accumulate experience over time. Employees' profiles, principles, and past learnings are auto-injected into agent prompts when dispatched
- **Feature**: `openmax employee` CLI — `add`, `list`, `show`, `edit`, `remove` commands for managing employee profiles
- **Feature**: Auto-learning — agents include `## Learnings` in their task reports, which are automatically extracted and appended to the employee's experience file
- **Feature**: `list_employees` MCP tool — lead agent can browse available employees and match them to tasks by specialty

## 0.9.17

- **Fix**: Forward `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` from Claude Code settings to lead agent subprocess — the SDK passes `--setting-sources ""` which skips settings files, causing login errors for users with settings-based auth

## 0.9.16

- **Improvement**: Faster startup — prompt building runs in parallel with SDK subprocess startup via ThreadPoolExecutor; git status and branch detection run concurrently with Popen
- **Improvement**: Spinner stays visible through full startup with stage labels (starting up → connecting → thinking)
- **Improvement**: Modern banner redesign — `⚡ openMax` with tagline, replacing basic reversed-cyan block

## 0.9.15

- **Fix**: Recognize API key configured in Claude Code settings (`~/.claude/settings.json` env) as valid authentication — users who set `ANTHROPIC_API_KEY` + base URL in settings instead of using `claude login` are no longer shown as unauthenticated

## 0.9.14

- **Improvement**: Auto-cleanup on exit — signal handler now removes openmax/* branches and worktrees on Ctrl+C/SIGTERM, not just on happy-path completion
- **Improvement**: Session auto-expiry — old sessions (>30 days) automatically pruned on startup
- **Chore**: Remove 61 trivial tests (import checks, dataclass defaults, constant counts) — 830 → 769 tests

## 0.9.13

- **Feature**: Support `@file` syntax for reading prompts from files — `openmax run @task.md` reads the file contents as the prompt. Works with multi-task mode and mixed inline/file arguments
- **Feature**: `openmax clean` command — removes residual branches, worktrees, task files, sockets, and (with `--all`) expired sessions

## 0.9.12

- **Chore**: Remove agent test artifacts (hello.py, fib.py, prime.py, stack.py) from repo
- **Fix**: Align dashboard and usage tests with compact output format

## 0.9.11

- **Refactor**: Declutter end-of-session output — consolidated redundant panels into a single clean Done banner, simplified Result panel and usage line, agent table only shown for multi-agent runs

## 0.9.10

- **Feature**: Auto-open Terminal.app for sub-agent panes on macOS — when no Kaku/Ghostty is available and not inside tmux, automatically opens a Terminal.app window attached to the tmux session. Lead agent output stays in the current terminal, sub-agents visible in the new window
- **Fix**: `terminal-tmux` now available as explicit `--pane-backend` choice
- **Fix**: Terminal.app launcher correctly opens a new window

## 0.9.7

- Skipped (re-published with terminal-tmux backend fix)

## 0.9.6

- **Fix**: Multi-task mode now renders a single unified UI instead of overlapping per-task banners. All user interaction (plan confirmation, ask_user) is serialized through a UICoordinator lock, preventing stdin conflicts between parallel lead agents
- **Fix**: Dashboard disabled in multi-task mode to prevent Rich Live display corruption

## 0.9.5

- **Perf**: Dispatch latency reduced ~1.5s per agent — faster ready delay (1.0s → 0.3s) and trust polling (6 × 0.2-0.4s → 3 × 0.15-0.3s)
- **Perf**: Monitoring + verification polling tightened — mailbox recv timeout 5s → 1s, verify backoff 0.5-1.5s → 0.2-1.0s
- **Perf**: Stronger stop instruction after inline monitor — aim for 2 LLM turns instead of unbounded

## 0.9.4

- **Feature**: Layered pane architecture — Kaku (UI) × tmux (grid engine). Kaku renders the window, tmux manages pane splits with automatic `select-layout tiled` grid. Zero-wait bootstrap: detach → get pane ID → attach UI
- **Fix**: Default to interactive `claude-code` mode so agent output is visible in panes (was using silent `claude -p` print mode)
- **Fix**: First agent reuses tmux session's default pane — no extra empty zsh window
- **Improvement**: Pluggable UI launcher registry — adding WezTerm/iTerm2 only requires one function

## 0.9.3

- **Feature**: Claude Code-style tree layout for agent dashboard — lightweight `├─`/`└─` tree connectors replace the dense Panel+Table layout, with nested activity lines (`⎿`) and compact summary footer
- **Feature**: Layered pane architecture — UI terminal (Kaku/Ghostty) × tmux grid engine. Kaku/Ghostty now open a window attached to a tmux session; tmux manages all pane splits with automatic `select-layout tiled` grid. Falls back to pure tmux when no UI terminal is available
- **Improvement**: Auto-detect prefers `kaku-tmux` on macOS when both are available, giving native UI + balanced grid layout

## 0.9.2

- **Feature**: AST-based style enforcement in quality mode — deterministic function length checking via stdlib `ast`, no new dependencies
- **Feature**: Challenger step added to quality workflow — write → review → challenge → rewrite cycle with mandatory pseudocode counter-design
- **Improvement**: Upgraded reviewer prompt — code density scoring, naming precision, composition pattern detection, AST violation integration
- **Improvement**: Rewrite prompt now has ranked objectives (mandatory → conditional → aspirational)
- **Fix**: Only committing steps (write, rewrite) merge; reviewer/challenger skip merge to preserve clean state transfer
- **Fix**: Reports explicitly persisted from worktrees between workflow steps

## 0.9.1

- **Fix**: `openmax status` no longer shows raw `HTTP Error 401: Unauthorized` when Claude Code OAuth token is expired — now shows a clear "credentials expired" message with re-auth guidance
- **Fix**: Refreshed OAuth tokens are persisted back to keychain, avoiding repeated refresh calls
- **Fix**: Handle both camelCase and snake_case token response formats from the OAuth endpoint

## 0.9.0

- **Multi-task execution**: Run multiple tasks concurrently with one command
  - `openmax run "task1" "task2" "task3"` — dispatches parallel lead agent sessions
  - `--project` flag maps tasks to registered projects
  - `ThreadPoolExecutor` with sliding window (default concurrency: 6)
  - Unified batch summary on completion
- **Project registry**: Manage multiple projects for cross-project workflows
  - `openmax projects add <path>` — register a project (auto-detects name from git remote)
  - `openmax projects list` — show all registered projects
  - `openmax projects remove <name>` — unregister a project
  - `openmax projects status` — git status across all projects
- **Smart task routing**: Auto-match task to registered project by keyword
  - "fix login in auth-service" → automatically routes to auth-service repo
  - Falls back to explicit `--project` flag or current directory
- **macOS notifications**: System notification on task completion/failure
  - Uses `osascript` — zero dependencies, works out of the box
- **Inline monitoring**: `submit_plan` blocks until all agents complete when no dependencies
  - Eliminates 2 LLM turns (~15-25s saved per session)
- **Positioning pivot**: From "parallel acceleration" to "AI team manager"
  - One command, multiple AI agents, zero babysitting

## 0.8.8

- **Performance**: Auto-verify pipeline + parallel optimizations — **up to 2.0x speedup** on xlarge parallel tasks
  - Auto-verify after merge: lint verification runs automatically when all agents finish, eliminating 2-3 LLM turns
  - Parallel verification: multiple lint/test commands run concurrently via `anyio.create_task_group`
  - Parallel auto-dispatch: root subtasks launched concurrently instead of sequentially
  - Adaptive verification polling: 0.5s→1.5s backoff replaces fixed 2s sleep
  - Deferred branch cleanup: batch cleanup after all agents finish (no longer blocks merge)
  - Auto-report completion: `report_completion` called automatically when all tasks done
  - Reduced ready delay: 3.0s→1.0s with pattern detection for Claude Code adapter
  - Faster trust dialog polling: 0.2s initial (was 0.5s)
  - Eliminated duplicate archetype matching in prompt builder
  - Prompt optimized: default skip research, speed-first directives, immediate monitoring after dispatch
- **Fix**: CI test reliability — `git init -b main` ensures consistent default branch name
- **Benchmark**: Results on xlarge complex-parallel (auth + pipeline + monitoring):
  - Best run: **2.0x** (oM 229s vs CC 468s)
  - Average: **1.4x** across multiple runs

## 0.8.7

- **Performance**: Batch monitoring + parallel git — reduce orchestration overhead
  - `wait_for_agent_message` now batches ALL completions (done + auto-merge) in one tool call
    - 3 agents finishing = 1 LLM turn instead of 3, saving ~10-18s of API overhead
  - Remove global `_git_lock` — worktree creation runs in parallel across agents
  - Per-target-branch merge lock replaces global lock (merges to same branch still serialized)
  - Updated lead agent prompt for batch monitoring protocol

## 0.8.6

- **Performance**: Reduce subprocess overhead in pane operations
  - Batch `alive_pane_ids()` replaces N×`is_pane_alive()` calls in reuse/auto-done loops (N subprocess calls → 1)
  - `refresh_states(force=True)` + `all_panes_summary()` avoids double `list_panes` subprocess call
  - Cleanup uses direct backend calls instead of cache layer
  - Config `_load()` cached with mtime check — eliminates redundant file I/O

## 0.8.5

- **Fix**: Auto-heal stale Kaku socket symlink after Kaku restart
  - Kaku doesn't update `default-fun.tw93.kaku` symlink on restart, breaking all CLI commands
  - Detect stale symlink via `KAKU_UNIX_SOCKET` env var and re-point automatically
  - One-time check on first `_run_kaku` call, zero overhead after

## 0.8.4

- **Performance**: Print-mode agents + skip redundant verification
  - Auto-dispatched agents use `claude-code-print` (non-interactive): dispatch 9s → 0.3s
  - Register `claude-code-print` adapter in built-in agent registry
  - Skip `run_verification` when all agents self-verified and auto-merged
  - Improved auto-dispatch prompts: include full user goal as context
  - Total orchestration overhead reduced to ~45s (was ~100s at v0.8.1)

## 0.8.3

- **Performance**: Zero-turn dispatch and merge — bypass LLM for mechanical operations
  - `submit_plan` auto-dispatches root subtasks: dispatch time 88s → 9s for 3 agents
  - `wait_for_agent_message` auto mark_done + merge on `done` signal: 0 LLM turns per subtask completion
  - Combined effect: **1.2x speedup** on xlarge tasks (232s vs 286s)
  - Lead agent token usage reduced ~40% (fewer API round-trips)

## 0.8.2

- **Performance**: Reduce lead agent monitoring overhead for faster task completion
  - Pre-propagate trust settings to worktrees — eliminates trust dialog in sub-agent panes
  - Shorten trust dialog poll from 10s to 4s max
  - Increase `wait_for_agent_message` default timeout to 60s — fewer wasted polling turns
  - Optimize system prompt: mailbox-first monitoring, batch mark+merge, combined verification
  - Lead agent turns reduced from ~14 to ~7 for single-subtask jobs
- **Benchmark**: Add complex parallel tasks and fix timeouts
  - New `parallel-microservices` task (3 independent Flask services)
  - New `complex-parallel` task (auth + pipeline + monitoring — xlarge)
  - Increase task timeouts to 600s for realistic openMax overhead
  - Log openMax stderr on benchmark failures
- **Result**: openMax 1.1x faster than Claude Code on xlarge tasks (auth+pipeline+monitoring)

## 0.8.1

- **Performance**: Hot-path optimizations for faster agent dispatch and monitoring
  - TTL-based `list_panes()` cache in PaneManager — eliminates redundant subprocess calls (~50ms each)
  - Remove redundant double `is_pane_alive()` check in `read_pane_output`
  - Adaptive backoff polling in `_wait_for_pane_ready` (0.15s→1.0s vs fixed 0.5s) — faster ready detection
  - Reduce `send_text` paste-to-enter delay from 0.5s to 0.15s (~350ms saved per prompt send)
  - Reduce `_find_pane_window` retry delay from 0.3s to 0.1s
  - Throttle session `meta.json` writes to once per 5 seconds (reduces file I/O during high-frequency events)

## 0.8.0

- **Feature**: Automated benchmark — compare Claude Code (single agent) vs openMax (multi-agent) completion times
  - `openmax benchmark run` — run SWE-bench style tasks through both systems, collect wall-clock time, tokens, cost
  - `openmax benchmark list` — browse available benchmark tasks with difficulty ratings
  - Built-in task suite: 3 tasks across difficulty levels (small/medium/large) — REST endpoint, module refactoring, full CRUD API
  - Rich terminal report with speedup factor, cost ratio, and pass/fail per runner
  - JSON report persistence to `.openmax/benchmarks/` for trend tracking
  - Task format is declarative YAML with setup_script, verify_script, and success_pattern
  - Custom task suites supported via `--tasks` flag
  - 17 new tests (748 total)
  - New dependency: `pyyaml>=6.0`

## 0.7.2

- **Feature**: Per-subtask token/usage tracking — track token consumption across all agents (lead + sub-agents)
  - `report_done` MCP tool accepts optional `input_tokens`, `output_tokens`, `cost_usd` for sub-agent self-reporting
  - Sub-agent prompts instruct agents to include token stats when available
  - Token data flows from mailbox done messages into SubTask fields, aggregated at session end
  - Post-run console output shows agent usage breakdown table (Task, Agent, Tokens, Cost, Source)
  - `openmax usage <session>` CLI displays per-subtask breakdown with total (lead + agents)
  - Session list view adds "Agents" column showing dispatched agent count
  - `usage.json` includes `subtask_usage` array and `total_session_cost_usd` — backward compatible
  - 13 new tests (731 total)

## 0.7.1

- **Remove**: TUI dashboard module (Textual-based) — not production-ready, causing runtime errors. Classic Rich status bar remains as the sole dashboard. Drops `textual` dependency and `--no-tui` CLI flag.

## 0.7.0

- **Feature**: Project Archetypes — domain-aware task decomposition system
  - 5 built-in archetypes: `web` (web app), `cli` (CLI tool), `api` (API service), `library` (Python package), `refactor` (codebase restructuring)
  - Two-stage matching: task classification (greenfield/modification/bugfix) → keyword scoring with ≥2 threshold
  - Archetype context injected into lead agent prompt — execution phases, brief hints, failure modes
  - Custom YAML archetypes via `.openmax/archetypes/*.yaml` (project-local) or `~/.config/openmax/archetypes/*.yaml` (global)
  - Dispatch briefs auto-enriched with archetype-specific hints and anti-patterns
  - Lead agent prompt updated with "Archetype-Guided Planning" section
  - 33 new tests (810 total), implemented by openMax itself (3 parallel agents, 13 min)

## 0.6.6

- **Fix**: Verification pane sets `PYTHONPATH=src/` so pytest can import the project without `pip install -e .` — eliminates first-attempt test failures

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
