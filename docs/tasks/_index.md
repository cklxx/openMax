# openMax Task Index

> Each row is a dispatchable task spec. Status: `pending` → `in_progress` → `done`.

## Priority Legend

- **P0**: Critical path — must ship first
- **P1**: High value, independent of P0
- **P2**: Medium value, may depend on P0/P1
- **P3**: Low priority or dependent on earlier phases

## Task Table

| ID | Title | Priority | Dependencies | Status | Key Files |
|----|-------|----------|--------------|--------|-----------|
| 4.1 | Stuck Detection | P0 | — | done | `tools.py`, `session_runtime.py` |
| 5.2 | Build Verification | P0 | — | done | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 1.1 | Structured Decomposition | P0 | — | done | `types.py`, `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 7.1 | Memory Eviction | P1 | — | pending | `memory/store.py`, `memory/_utils.py` |
| 10.1 | Failure Auto-Retry | P1 | — | pending | `tools.py`, `types.py`, `lead_agent.md`, `pane_manager.py` |
| 6.3 | Token Tracking | P1 | — | pending | `lead_agent/core.py`, `session_runtime.py` |
| 8.1 | Phase Gating | P1 | — | pending | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md`, `dashboard.py` |
| 3.1 | Context Injection | P2 | 1.1 | pending | `tools.py` |
| 4.2 | Dashboard Elapsed Time | P2 | — | pending | `dashboard.py`, `tools.py`, `types.py` |
| 5.1 | Git Conflict Detection | P2 | — | pending | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 2.1 | Recommendation Accuracy | P2 | — | pending | `tools.py`, `memory/rankings.py` |
| 1.2 | Dependency Scheduling | P3 | 1.1 | pending | `tools.py`, `types.py` |
| 2.2 | Load Balancing | P3 | — | pending | `tools.py`, `types.py` |
| 3.2 | Context Budget | P3 | 3.1 | pending | `tools.py` |
| 4.3 | Progress Readability | P3 | — | pending | `dashboard.py` |
| 5.3 | Code Consistency | P3 | 5.2 | pending | `tools.py`, `linter_detect.py` (new) |
| 6.1 | Acceleration Ratio | P3 | 6.3 | pending | `types.py`, `tools.py`, `session_runtime.py` |
| 6.2 | Orchestration Overhead | P3 | 6.3 | pending | `session_runtime.py`, `tools.py` |
| 7.2 | Recall Usefulness | P3 | 7.1 | pending | `memory/store.py`, `tools.py`, `lead_agent.md` |
| 7.3 | Error Pattern Learning | P3 | 7.1 | pending | `tools.py`, `memory/store.py` |
| 8.2 | Research Phase | P3 | 8.1 | pending | `tools.py`, `lead_agent.md` |
| 8.3 | Verify Phase | P3 | 5.2, 8.1 | pending | `lead_agent.md` |
| 9.1 | Plan Approval | P3 | 8.1 | pending | `tools.py`, `cli.py`, `lead_agent/core.py` |
| 9.2 | Acceptance Confirmation | P3 | 8.3 | pending | `lead_agent.md`, `lead_agent/core.py` |
| 10.2 | Resume Enhancement | P3 | — | pending | `session_runtime.py`, `lead_agent/core.py` |
| 11.1 | Branch Isolation | P1 | — | done | `tools.py`, `lead_agent.md` |

## Dispatch Waves

### Wave 1 (P0 — three independent items)

Conflict analysis: all three add entries to `ALL_TOOLS`, `formatting.py`, and `session_runtime.py`'s `reconstruct_plan`. Strategy: dispatch 4.1 first (smallest), then 5.2 + 1.1 in parallel.

### Wave 2 (P1 — four independent items)

7.1, 10.1, 6.3, 8.1 — no mutual dependencies, can all run in parallel.

### Wave 3+ (P2/P3)

Follow dependency chains from the table above.

## Dependency Graph

```
1.1 Structured Decomposition
  └── 1.2 Dependency Scheduling
  └── 3.1 Context Injection
      └── 3.2 Context Budget

4.1 Stuck Detection (independent)

5.1 Git Conflict Detection (independent)
5.2 Build Verification (independent)
  └── 5.3 Code Consistency
  └── 8.3 Verify Phase

6.3 Token Tracking (independent)
  └── 6.1 Acceleration Ratio
  └── 6.2 Orchestration Overhead

7.1 Memory Eviction (independent)
  └── 7.2 Recall Usefulness
  └── 7.3 Error Pattern Learning

8.1 Phase Gating (independent)
  └── 8.2 Research Phase
  └── 8.3 Verify Phase
  └── 9.1 Plan Approval
      └── 9.2 Acceptance Confirmation

10.1 Failure Auto-Retry (independent)
10.2 Resume Enhancement (independent)

11.1 Branch Isolation (independent)
```

## Completed (chronological)

1. **4.1** Stuck Detection
2. **5.2** Build Verification
3. **1.1** Structured Decomposition
4. **7.1** Memory Eviction
5. **10.1** Failure Auto-Retry
6. **6.3** Token Tracking
7. **8.1** Phase Gating
8. **3.1** Context Injection
9. **4.2** Dashboard Elapsed Time
10. **5.1** Git Conflict Detection
11. **11.1** Branch Isolation
