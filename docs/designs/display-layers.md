# Two-Layer Display for openMax Dashboard

## Layer 1 — Compact Status Table (default)

Columns: badge | name | agent | activity | elapsed

- **activity** replaces pane_id column
- Shows last pane output line (truncated to 30 chars) for running tasks
- Shows "done" for completed tasks
- Empty for pending tasks
- Zero LLM calls — purely derived from `pane_activity` data

## Layer 2 — Verbose Mode (`--verbose` / `-v`)

Adds detail rows beneath each running/done subtask:
- Retains pane_id as an additional column
- Shows dispatch prompt first line (dim, indented)
- Activated via `--verbose` flag on `run`, `manage`, `loop` commands

## Post-hoc Layer 2 — `openmax inspect`

Enhanced subtask table with:
- Elapsed column (computed from started_at/finished_at)
- Notes column (completion_notes truncated to 40 chars)
