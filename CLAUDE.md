# openMax — Claude Code Instructions

## STOP — Read this first

* Priority: **correctness > simplicity > speed**.
* Before ANY code change, run: `git diff --stat` + `git log --oneline -5`. If diffs touch unrelated files or revert intended logic, investigate before proceeding.
* On ANY user correction, codify a preventive rule before resuming (add to this file or `docs/`).

---

## About

openMax is a multi-AI-agent orchestration hub. It dispatches interactive AI agents (Claude Code, Codex, OpenCode) into Kaku terminal panes, monitors them, and reports results.

---

## Non-negotiable standards

- Lint: `ruff check src/ tests/` and `ruff format src/ tests/` must pass before commit.
- Tests: `pytest tests/ -v` must pass before commit.
- Line length: 100 chars max.
- Python 3.10+ — use `X | Y` union syntax, not `Union[X, Y]`.
- Delete dead code outright. No `# deprecated`, `_unused`, or commented-out legacy blocks.
- Trust type/caller invariants; avoid unnecessary defensive code.
- Modify only files relevant to the task.

---

## Memory loading

**Always-load** (every conversation start):
1. `docs/memory/long-term.md` — durable cross-session rules
2. Latest 2 entries from `docs/experience/errors/` (by date DESC)
3. Latest 2 entries from `docs/experience/wins/` (by date DESC)

**On-demand**: load full experience entries only when summaries are insufficient.

---

## Conditional context loading

Only read these when the trigger matches — do not bulk-load all docs.

| Trigger | Read |
|---|---|
| Task touches lead agent behavior | `src/openmax/lead_agent/prompts/lead_agent.md` |
| Task touches pane/window management | `src/openmax/pane_manager.py` |
| Task touches memory system | `src/openmax/memory/` package |
| Task touches CLI entry points | `src/openmax/cli.py` |
| Task touches agent adapters | `src/openmax/adapters/` |
| Planning a new feature | `docs/tasks/` for existing specs |
| Bug caused a pattern | Record in `docs/experience/errors/` before fixing |
| User gives correction | Codify rule in this file or `docs/experience/errors/` |
| Technique worked well | Record in `docs/experience/wins/` |
| Publishing a release | See §Publishing below |

Default route (no trigger): inspect target files and neighboring patterns → implement minimal correct change → lint + test → commit.

---

## Key concepts

- **Lead agent** runs via `claude-agent-sdk`. It has NO file access — it works only through custom MCP tools (`dispatch_agent`, `read_pane_output`, `send_text_to_pane`, etc.).
- **System prompt** lives in `src/openmax/lead_agent/prompts/lead_agent.md`. Edit the markdown, not inline strings.
- **PaneManager** tracks window/pane topology. All agents share one Kaku window with auto grid layout.
- **CLAUDECODE env var** must be unset in spawned panes to avoid nested-session errors. Handled by `_wrap_command_clean_env`.
- **send_text** uses paste + delayed `\r` via stdin pipe to submit in interactive CLIs.

---

## Project structure

```
src/openmax/
├── cli.py              # CLI entry (click)
├── lead_agent/         # Lead agent orchestration package
│   ├── types.py        # TaskStatus, SubTask, PlanResult, LeadAgentStartupError
│   ├── tools.py        # @tool functions + runtime helpers
│   ├── formatting.py   # Text formatting and tool-use display
│   ├── core.py         # run_lead_agent, prompt building, SDK client loop
│   └── prompts/
│       └── lead_agent.md  # Lead agent system prompt
├── memory/             # Workspace memory system package
│   ├── models.py       # MemoryEntry, MemoryContext, dataclasses
│   ├── taxonomy.py     # Task classification and prediction
│   ├── store.py        # MemoryStore: persistence, context, lessons
│   ├── rankings.py     # Agent ranking and strategy derivation
│   └── _utils.py       # Constants, serialization helpers
├── pane_manager.py     # Kaku window/pane lifecycle
├── kaku.py             # Kaku detection + auto-install
├── session_runtime.py  # Session persistence + context recovery
└── adapters/           # Agent CLI adapters (claude-code, codex, opencode)
```

---

## Workflow

- Small incremental commits with clear messages.
- Run lint + tests before committing.
- The system prompt (`lead_agent.md`) is the most important file — keep refining it.

---

## Publishing

```bash
# Bump version in pyproject.toml and src/openmax/__init__.py
python -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD=<token> python -m twine upload dist/openmax-<version>*
```
