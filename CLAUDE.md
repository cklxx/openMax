# openMax — Claude Code Instructions

## About

openMax is a multi-AI-agent orchestration hub. It dispatches interactive AI agents (Claude Code, Codex, OpenCode) into Kaku terminal panes, monitors them, and reports results.

## Project structure

```
src/openmax/
├── cli.py              # CLI entry (click)
├── lead_agent/         # Lead agent orchestration package
│   ├── __init__.py     # Public API re-exports
│   ├── types.py        # TaskStatus, SubTask, PlanResult, LeadAgentStartupError
│   ├── tools.py        # @tool functions + runtime helpers
│   ├── formatting.py   # Text formatting and tool-use display
│   ├── core.py         # run_lead_agent, prompt building, SDK client loop
│   └── prompts/
│       └── lead_agent.md  # Lead agent system prompt (edit this, not code)
├── memory/             # Workspace memory system package
│   ├── __init__.py     # Public API re-exports
│   ├── models.py       # MemoryEntry, MemoryContext, dataclasses
│   ├── taxonomy.py     # Task classification and prediction logic
│   ├── store.py        # MemoryStore core: persistence, context, lessons
│   ├── rankings.py     # Agent ranking, evaluation, strategy derivation
│   └── _utils.py       # Constants, serialization, coercion helpers
├── pane_manager.py     # Kaku window/pane lifecycle
├── kaku.py             # Kaku detection + auto-install
├── session_runtime.py  # Session persistence + context recovery
└── adapters/           # Agent CLI adapters (claude-code, codex, opencode)
```

## Standards

- Priority: **correctness > simplicity > speed**.
- Lint: `ruff check src/ tests/` and `ruff format src/ tests/` must pass before commit.
- Tests: `pytest tests/ -v` must pass before commit.
- Line length: 100 chars max.
- Python 3.10+ — use `X | Y` union syntax, not `Union[X, Y]`.

## Key concepts

- **Lead agent** runs via `claude-agent-sdk`. It has NO file access — it works only through custom MCP tools (`dispatch_agent`, `read_pane_output`, `send_text_to_pane`, etc.).
- **System prompt** lives in `src/openmax/lead_agent/prompts/lead_agent.md`. Edit the markdown file, not inline strings.
- **PaneManager** tracks window/pane topology. All agents share one Kaku window with auto grid layout.
- **CLAUDECODE env var** must be unset in spawned panes to avoid nested-session errors. This is handled by `_wrap_command_clean_env`.
- **send_text** uses paste + delayed `\r` via stdin pipe to submit in interactive CLIs.

## Workflow

- Commit with clear messages. Small incremental commits.
- Run lint + tests before committing.
- Don't over-engineer. The system prompt is the most important file — keep refining it.

## Publishing

```bash
# Bump version in pyproject.toml and src/openmax/__init__.py
python -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD=<token> python -m twine upload dist/openmax-<version>*
```
