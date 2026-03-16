# openMax Task Index

> Each row is a dispatchable task spec. Status: `pending` → `in_progress` → `done`.

## Core Problem

Single agent hits two hard ceilings:

1. **Context window capacity** — one agent cannot hold full state of a large project. A C compiler case required 16 agents because no single agent could simultaneously understand lexing, codegen, and bootstrapping.
2. **Nonlinear reasoning degradation** — beyond a complexity threshold, single-agent accuracy drops off a cliff. Divide-and-conquer is the only escape: split one impossible-for-Opus task into 5 feasible-for-Opus subtasks.

## Core Goals (by priority)

1. **Parallel acceleration** — compress serial execution time to 1/N (N = agent count), bounded by critical path of the dependency graph. Target: weeks → days.
2. **Error isolation** — one agent's mistake must not pollute another's workspace. Independent panes + separate working contexts are the mechanism.
3. **Adversarial quality** — multiple agents challenge each other's assumptions, eliminating anchoring bias. Conclusions that survive debate are more reliable than serial investigation.

## Unsolved Problems (industry-wide)

No mainstream framework (Claude Teams / Kimi Swarm / LangGraph / CrewAI) has solved these:

| Problem | Description | Related Tasks |
|---------|-------------|---------------|
| **File conflict** | No agent-native branch isolation or auto-merge — only workarounds (file locks, ownership planning) | 5.1, 11.1 |
| **Context cold start** | Spawned agents don't inherit lead's history; spawn prompt quality determines output quality; no standardized context compression protocol | 3.1, 3.2, 11.2 |
| **Cost controllability** | N-agent team costs 3-4x single agent; inter-agent communication grows O(N²); no convergence mechanism to cap runaway token spend | 6.3, 11.3 |

---

## Task Pillars

Tasks are organized by the core goal they serve, not by implementation order.

### Pillar A — Task Decomposition & Scheduling (→ Parallel Acceleration)

| ID | Title | Pri | Deps | Status | Key Files |
|----|-------|-----|------|--------|-----------|
| 1.1 | Structured Decomposition | P0 | — | done | `types.py`, `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 1.2 | Dependency Scheduling | P3 | 1.1 | pending | `tools.py`, `types.py` |
| 2.2 | Load Balancing | P3 | — | pending | `tools.py`, `types.py` |

### Pillar B — Context & Knowledge (→ Quality)

| ID | Title | Pri | Deps | Status | Key Files |
|----|-------|-----|------|--------|-----------|
| 3.1 | Context Injection | P2 | 1.1 | done | `tools.py` |
| 7.1 | Memory Eviction | P1 | — | done | `memory/store.py`, `memory/_utils.py` |
| 2.1 | Recommendation Accuracy | P2 | — | pending | `tools.py`, `memory/rankings.py` |
| 3.2 | Context Budget | P3 | 3.1 | pending | `tools.py` |
| 7.2 | Recall Usefulness | P3 | 7.1 | pending | `memory/store.py`, `tools.py`, `lead_agent.md` |
| 7.3 | Error Pattern Learning | P3 | 7.1 | pending | `tools.py`, `memory/store.py` |
| 11.2 | Context Compression Protocol | P2 | 3.1 | pending | `tools.py`, `lead_agent/core.py`, `lead_agent.md` |

### Pillar C — Execution Reliability (→ Error Isolation)

| ID | Title | Pri | Deps | Status | Key Files |
|----|-------|-----|------|--------|-----------|
| 4.1 | Stuck Detection | P0 | — | done | `tools.py`, `session_runtime.py` |
| 10.1 | Failure Auto-Retry | P1 | — | done | `tools.py`, `types.py`, `lead_agent.md`, `pane_manager.py` |
| 8.1 | Phase Gating | P1 | — | done | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md`, `dashboard.py` |
| 8.2 | Research Phase | P3 | 8.1 | pending | `tools.py`, `lead_agent.md` |
| 8.3 | Verify Phase | P3 | 5.2, 8.1 | pending | `lead_agent.md` |
| 9.1 | Plan Approval | P3 | 8.1 | pending | `tools.py`, `cli.py`, `lead_agent/core.py` |
| 9.2 | Acceptance Confirmation | P3 | 8.3 | pending | `lead_agent.md`, `lead_agent/core.py` |
| 10.2 | Resume Enhancement | P3 | — | pending | `session_runtime.py`, `lead_agent/core.py` |
| 11.1 | Branch Isolation | P1 | — | done | `tools.py`, `lead_agent.md` |

### Pillar D — Quality Assurance (→ Adversarial Quality)

| ID | Title | Pri | Deps | Status | Key Files |
|----|-------|-----|------|--------|-----------|
| 5.2 | Build Verification | P0 | — | done | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 5.1 | Git Conflict Detection | P2 | — | done | `tools.py`, `formatting.py`, `session_runtime.py`, `lead_agent.md` |
| 5.3 | Code Consistency | P3 | 5.2 | pending | `tools.py`, `linter_detect.py` (new) |
| 11.1 | Branch Isolation & Auto-Merge | P1 | 5.1 | pending | `pane_manager.py`, `tools.py`, `lead_agent.md` |

### Pillar E — Observability & Cost (→ Cost Controllability)

| ID | Title | Pri | Deps | Status | Key Files |
|----|-------|-----|------|--------|-----------|
| 6.3 | Token Tracking | P1 | — | done | `lead_agent/core.py`, `session_runtime.py` |
| 4.2 | Dashboard Elapsed Time | P2 | — | done | `dashboard.py`, `tools.py`, `types.py` |
| 4.3 | Progress Readability | P3 | — | pending | `dashboard.py` |
| 6.1 | Acceleration Ratio | P3 | 6.3 | pending | `types.py`, `tools.py`, `session_runtime.py` |
| 6.2 | Orchestration Overhead | P3 | 6.3 | pending | `session_runtime.py`, `tools.py` |
| 11.3 | Cost Convergence & Budget Control | P2 | 6.3 | pending | `lead_agent/core.py`, `tools.py`, `types.py` |

---

## Progress Summary

| Status | Count |
|--------|-------|
| done | 10 |
| pending | 17 |
| **total** | **27** |

### Completed (chronological)

1. 4.1 Stuck Detection
2. 5.2 Build Verification
3. 1.1 Structured Decomposition
4. 6.3 Token Tracking
5. 3.1 Context Injection
6. 4.2 Dashboard Elapsed Time
7. 5.1 Git Conflict Detection
8. 7.1 Memory Eviction
9. 8.1 Phase Gating
10. 10.1 Failure Auto-Retry

---

## Dispatch Waves

### Wave 1 — P0 (done)

4.1, 5.2, 1.1 — all completed.

### Wave 2 — P1 (done)

7.1, 10.1, 6.3, 8.1 — all completed.

### Wave 3 — Next Up (P1–P2, ready to dispatch)

| ID | Title | Why now |
|----|-------|---------|
| 11.1 | Branch Isolation & Auto-Merge | Unlocks safe multi-agent parallel edits — the #1 unsolved problem |
| 11.2 | Context Compression Protocol | Directly addresses context cold start — #2 unsolved problem |
| 11.3 | Cost Convergence & Budget Control | Addresses cost controllability — #3 unsolved problem |
| 2.1 | Recommendation Accuracy | Independent, improves agent selection quality |

Conflict analysis: 11.1 and 11.3 both touch `tools.py` — dispatch sequentially or partition file sections. 11.2 is independent.

### Wave 4 — P3 (after Wave 3 dependencies met)

Follow dependency chains: 1.2, 2.2, 3.2, 4.3, 5.3, 6.1, 6.2, 7.2, 7.3, 8.2, 8.3, 9.1, 9.2, 10.2.

---

## Dependency Graph

```
Pillar A — Decomposition
  1.1 Structured Decomposition ✅
    └── 1.2 Dependency Scheduling
  2.2 Load Balancing (independent)

Pillar B — Context
  3.1 Context Injection ✅
    └── 3.2 Context Budget
    └── 11.2 Context Compression Protocol
  7.1 Memory Eviction ✅
    └── 7.2 Recall Usefulness
    └── 7.3 Error Pattern Learning
  2.1 Recommendation Accuracy (independent)

Pillar C — Reliability
  4.1 Stuck Detection ✅
  10.1 Failure Auto-Retry ✅
  8.1 Phase Gating ✅
    └── 8.2 Research Phase
    └── 8.3 Verify Phase → 9.2 Acceptance Confirmation
    └── 9.1 Plan Approval
  10.2 Resume Enhancement (independent)

Pillar D — Quality
  5.2 Build Verification ✅
    └── 5.3 Code Consistency
    └── 8.3 Verify Phase (cross-pillar)
  5.1 Git Conflict Detection ✅
    └── 11.1 Branch Isolation & Auto-Merge

Pillar E — Observability
  6.3 Token Tracking ✅
    └── 6.1 Acceleration Ratio
    └── 6.2 Orchestration Overhead
    └── 11.3 Cost Convergence & Budget Control
  4.2 Dashboard Elapsed Time ✅
  4.3 Progress Readability (independent)
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
