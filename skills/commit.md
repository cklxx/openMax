---
description: Run lint + tests, then commit staged changes with a concise message
allowed-tools: Bash
---

Run the following in order. Stop immediately if any step fails and report the error.

1. `ruff check src/ tests/ && ruff format src/ tests/` — fix any issues before proceeding
2. `pytest tests/ -v` — all tests must pass
3. `git diff --staged --stat` — confirm staged files are what you expect
4. Commit with: `git commit -m "<concise message describing WHY, not what>"`

Do not use `--no-verify`. Do not amend existing commits. Create a new commit.

$ARGUMENTS
