# openMax — Claude Code Instructions

## STOP — Read this first

* Priority: **correctness > simplicity > speed**.
* Before ANY code change, run: `git diff --stat` + `git log --oneline -5`. Investigate if diffs touch unrelated files.
* On ANY user correction, codify a preventive rule before resuming.

---

## About

openMax is a multi-AI-agent orchestration hub. It dispatches interactive AI agents (Claude Code, Codex, OpenCode) into terminal panes, monitors them, and reports results.

---

## Code style (MANDATORY)

- Max function body: **15 lines**. Extract or redesign if exceeded.
- No comments that restate code. Only "why" comments for non-obvious decisions.
- Prefer composition over inheritance. Prefer data transforms over mutation.
- Every abstraction must justify itself: used <2 places → inline it.
- No TODOs in committed code. Delete dead code paths immediately.
- Type signatures are documentation. Verbose names > comments.
- When two approaches are equally correct, pick the one with fewer moving parts.

Reference density (Python equivalent):
```python
def authenticate(token: str, secret: str) -> Result[Claims, AuthError]:
    return (
        decode(token)
        .bind(verify(secret))
        .map_err(to_auth_error)
    )
```
No wrapper classes. No builders. No config objects. Transform pipelines.

---

## Non-negotiable standards

- Lint: `ruff check src/ tests/` and `ruff format src/ tests/` must pass before commit.
- Tests: `pytest tests/ -v` must pass before commit.
- Line length: 100 chars max.
- Python 3.10+ — use `X | Y` union syntax, not `Union[X, Y]`.
- Delete dead code outright. No `# deprecated`, `_unused`, or commented-out blocks.
- Trust type/caller invariants; avoid unnecessary defensive code.
- Modify only files relevant to the task.

---

## Testing discipline (MANDATORY for every feature)

Every new feature or fix requires tests at three levels before the PR is considered done:

| Level | What | File pattern |
|---|---|---|
| **Unit** | Core logic in isolation — pure functions, data structures, edge cases | `tests/test_<module>.py` |
| **CLI** | Command wiring — CliRunner + monkeypatch boundary, correct args forwarded | `tests/test_cli.py` |
| **Integration** | End-to-end data flow — real filesystem (tmp_path), mocked external calls only | `tests/test_<module>.py` or `tests/test_ci_smoke.py` |

### Rules
- Mock **only at the boundary**: `run_lead_agent`, `PaneManager`, external processes, `_run_kaku`. Never mock internal logic.
- Each test has **one assertion focus**. Split scenarios into separate test functions.
- Cover: happy path + at least one failure/edge case (error handling, empty input, pruning threshold).
- Use `pytest` fixtures (`tmp_path`, `monkeypatch`) — no global mutable state.
- Test file for `src/openmax/foo.py` lives at `tests/test_foo.py`.

### Checklist before committing a feature
```
[ ] Unit tests for the new module / function
[ ] CLI test if a new command or flag was added
[ ] Integration test verifying the full data flow (tape written? context injected? pruning triggered?)
[ ] ruff check + ruff format pass
[ ] pytest tests/ passes (all, not just new tests)
```

---

## Memory loading

**Always-load** (every conversation start):
1. `docs/memory/long-term.md`
2. Latest 2 entries from `docs/experience/errors/` (by date DESC)
3. Latest 2 entries from `docs/experience/wins/` (by date DESC)

**On-demand**: load full experience entries only when summaries are insufficient.

---

## Conditional context loading

Only read when the trigger matches — do not bulk-load.

| Trigger | Read |
|---|---|
| Lead agent behavior | `src/openmax/lead_agent/prompts/lead_agent.md` |
| Pane/window management | `src/openmax/pane_manager.py`, `src/openmax/pane_backend.py` |
| Memory system | `src/openmax/memory/` package |
| CLI entry points | `src/openmax/cli.py` |
| Agent adapters | `src/openmax/adapters/` |
| Planning a feature | `docs/tasks/` for existing specs |
| Bug caused a pattern | Record in `docs/experience/errors/` before fixing |
| User correction | Codify rule here or in `docs/experience/errors/` |
| Technique worked well | Record in `docs/experience/wins/` |
| Publishing a release | See §Publishing below |

Default route: inspect target files → implement minimal correct change → lint + test → commit.

---

## Key concepts

- **Lead agent** runs via `claude-agent-sdk`. No file access — works only through MCP tools (`dispatch_agent`, `read_pane_output`, `send_text_to_pane`, etc.).
- **System prompt** lives in `src/openmax/lead_agent/prompts/lead_agent.md`. Edit the markdown, not inline strings.
- **PaneManager** tracks window/pane topology. All agents share one terminal window with auto grid layout.
- **CLAUDECODE env var** must be unset in spawned panes. Handled by `_wrap_command_clean_env`.
- **send_text** uses paste + delayed `\r` via stdin pipe to submit in interactive CLIs.

---

## Project structure

Source lives in `src/openmax/`. Discover layout via `find src/openmax -type f -name '*.py' | sort`.

Key packages: `lead_agent/` (orchestration), `memory/` (workspace memory), `adapters/` (agent CLI adapters).

---

## Workflow

- Small incremental commits with clear messages.
- Lint + test before every commit.
- The system prompt (`lead_agent.md`) is the most important file — keep refining it.

---

## Publishing

```bash
# Bump version in pyproject.toml and src/openmax/__init__.py
python -m build
python -m twine upload dist/openmax-<version>*
```

