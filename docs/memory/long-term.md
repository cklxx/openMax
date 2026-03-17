# Long-term Memory — Durable Rules

Cross-session rules that apply to all openMax work. Only add rules here that have been validated by repeated experience.

## Architecture rules

- Lead agent has NO file access — it works only through MCP tools. Never add file-system calls to lead agent code.
- CLAUDECODE env var must be unset in spawned panes. Always use `_wrap_command_clean_env`.
- All agents share one Kaku window. PaneManager handles grid layout automatically.
- send_text uses paste + delayed `\r` — do not change the timing without testing on all adapters.

## Code rules

- The system prompt (`lead_agent.md`) is the most important file. Changes there have the highest impact-to-effort ratio.
- Memory entries are append-only JSON per workspace. Eviction runs on write when capacity is exceeded.
- Pinned entries are never evicted — use sparingly for critical lessons.

## Process rules

- Always run `ruff check` + `ruff format` + `pytest` before commit.
- When a bug is caused by a pattern, record it in `docs/experience/errors/` so the same mistake isn't repeated.
- When a technique works well, record it in `docs/experience/wins/`.

## Testing rules

Every feature requires tests at three levels before it ships:
1. **Unit** — pure logic in isolation (`tests/test_<module>.py`)
2. **CLI** — CliRunner + monkeypatch, verify args forwarded correctly (`tests/test_cli.py`)
3. **Integration** — real filesystem via `tmp_path`, mock only external boundaries

Mock only at the boundary (run_lead_agent, PaneManager, _run_kaku, external processes). Never mock internal logic. Each test function has one assertion focus. Cover happy path + at least one failure/edge case. See CLAUDE.md §Testing discipline for the full checklist.
