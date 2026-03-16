# Changelog

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
