# Behavior-Driven Optimization via Real Run Observation

**Date:** 2026-03-22
**Category:** process
**Severity:** P0

## What happened

Ran openMax 5 times with real tasks, observing lead agent management behavior. Found and fixed three critical runtime bugs that no amount of static analysis would have caught:

1. **Verification pane exit race** — `bash -c "cmd; echo marker"` exits before 2s polling captures the marker. Fix: add `sleep 5` after marker.
2. **Verification pane wrong cwd** — `_run_single_check` never passed `cwd=runtime.cwd`, so verification ran in wrong directory. Fix: one-line parameter addition.
3. **Verification failure rationalization** — Lead agent saw "FAIL" but explained it away as "output capture limits". Fix: prompt now has mandatory action table per verification status, plus "never rationalize away non-pass results" rule.

## Metrics across 5 runs

| Version | Turns | Time | Cost | Verification |
|---------|-------|------|------|-------------|
| v0.6.2 (pre-fix) | 27 | 5m34s | $0.48 | FAIL → ignored |
| v0.6.2 (multi) | 65 | 7m04s | $1.01 | FAIL → ignored |
| v0.6.3 | 51 | 5m29s | $0.79 | inconclusive × 4 |
| v0.6.4 | 39 | 9m40s | $0.46 | FAIL → retried → pass |
| v0.6.5 | 20 | 3m15s | $0.24 | pass (lint) + pass (test w/ pip) |

## Key insight

**Token metrics lie. Run the system and watch it.** The v0.6.1-v0.6.2 optimizations saved tokens but didn't fix the most impactful bug: verification never actually worked. Only by running real tasks and reading the output did we discover the exit-before-capture race, the missing cwd, and the lead agent rationalizing away failures.

## Remaining issue

Verification pane needs `pip install -e .` before pytest works — the editable install from the main process doesn't propagate to spawned panes. Options: (a) run pip install in verification pane before tests, (b) set PYTHONPATH in verification env, (c) detect and skip when package is already installed.

## Reuse

Always validate prompt changes with real runs. Static analysis (line counts, token budgets) is necessary but not sufficient. The real test is: does the lead agent make the right management decisions when things go wrong?
