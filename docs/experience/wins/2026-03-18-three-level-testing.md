# Three-Level Testing Discipline

**Date:** 2026-03-18
**Category:** process
**Severity:** P1

## What happened

Established a mandatory three-level testing pattern (unit / CLI / integration) for every new feature. Applied it retroactively to: `loop` command, session event pruning, and KakuPaneBackend retry. Went from 255 → 280 tests with full coverage of every new behavior.

## Key insight

The three levels catch different bugs:
- **Unit** catches logic errors in isolation (wrong context format, wrong prune threshold)
- **CLI** catches wiring bugs (loop_context not forwarded, wrong flag name)
- **Integration** catches end-to-end data flow bugs (tape not written, context not injected on iter 2)

All three are needed. Unit alone misses integration holes; integration alone is slow and hard to debug.

## Reuse

Apply this pattern to every new feature. The checklist in CLAUDE.md §Testing discipline is the gate — no feature ships without all three levels passing.
