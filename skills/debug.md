---
description: Diagnose a failing test or error, fix the root cause, and commit
allowed-tools: Bash, Glob, Grep, Read, Edit
---

**Failure context:** $ARGUMENTS

Debugging protocol:
1. Reproduce the failure: run the exact failing command and capture full output
2. Identify root cause — trace the error to its source, not just the symptom
3. Fix the minimal set of lines needed — do not refactor surrounding code
4. Re-run the failing command to confirm fix
5. Run `pytest tests/ -v` to confirm no regressions
6. Run `ruff check src/ tests/` to confirm no lint issues
7. Commit with message: `fix: <what was broken and why>`

Do not apply speculative fixes. If the root cause is unclear, report findings rather than guessing.
