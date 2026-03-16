# openMax Roadmap

> High-level overview of planned improvements. Each item has a detailed, agent-dispatchable spec in `docs/tasks/`.

---

## Priority Summary

| Priority | ID | Title | Spec | Status |
|----------|----|-------|------|--------|
| **P0** | 4.1 | Stuck Detection | [`p0_4.1_stuck_detection.md`](tasks/p0_4.1_stuck_detection.md) | done |
| **P0** | 5.2 | Build Verification | [`p0_5.2_build_verification.md`](tasks/p0_5.2_build_verification.md) | done |
| **P0** | 1.1 | Structured Decomposition | [`p0_1.1_structured_decomposition.md`](tasks/p0_1.1_structured_decomposition.md) | done |
| **P1** | 7.1 | Memory Eviction | [`p1_7.1_memory_eviction.md`](tasks/p1_7.1_memory_eviction.md) | done |
| **P1** | 10.1 | Failure Auto-Retry | [`p1_10.1_failure_auto_retry.md`](tasks/p1_10.1_failure_auto_retry.md) | done |
| **P1** | 6.3 | Token Tracking | [`p1_6.3_token_tracking.md`](tasks/p1_6.3_token_tracking.md) | done |
| **P1** | 8.1 | Phase Gating | [`p1_8.1_phase_gating.md`](tasks/p1_8.1_phase_gating.md) | done |
| **P2** | 3.1 | Context Injection | [`p2_3.1_context_injection.md`](tasks/p2_3.1_context_injection.md) | done |
| **P2** | 4.2 | Dashboard Elapsed Time | [`p2_4.2_dashboard_elapsed_time.md`](tasks/p2_4.2_dashboard_elapsed_time.md) | done |
| **P2** | 5.1 | Git Conflict Detection | [`p2_5.1_git_conflict_detection.md`](tasks/p2_5.1_git_conflict_detection.md) | done |
| **P2** | 2.1 | Recommendation Accuracy | spec pending | pending |
| **P3** | 1.2 | Dependency Scheduling | spec pending | pending |
| **P3** | 2.2 | Load Balancing | spec pending | pending |
| **P3** | 3.2 | Context Budget | spec pending | pending |
| **P3** | 4.3 | Progress Readability | spec pending | pending |
| **P3** | 5.3 | Code Consistency | spec pending | pending |
| **P3** | 6.1 | Acceleration Ratio | spec pending | pending |
| **P3** | 6.2 | Orchestration Overhead | spec pending | pending |
| **P3** | 7.2 | Recall Usefulness | spec pending | pending |
| **P3** | 7.3 | Error Pattern Learning | spec pending | pending |
| **P3** | 8.2 | Research Phase | spec pending | pending |
| **P3** | 8.3 | Verify Phase | spec pending | pending |
| **P3** | 9.1 | Plan Approval | spec pending | pending |
| **P3** | 9.2 | Acceptance Confirmation | spec pending | pending |
| **P3** | 10.2 | Resume Enhancement | spec pending | pending |

## Phases

### Phase 1: Task Decomposition Engine
- **1.1** Structured task decomposition via `submit_plan` tool
- **1.2** Dependency-aware scheduling in `dispatch_agent`

### Phase 2: Task Assignment & Agent Selection
- **2.1** Auto-use `get_agent_recommendations` in dispatch
- **2.2** Load-aware agent balancing

### Phase 3: Context Passing
- **3.1** Auto-inject file paths, predecessor notes, and relevant lessons
- **3.2** Token budget for injected context

### Phase 4: Execution Monitoring
- **4.1** Stuck detection via output hash tracking
- **4.2** Per-subtask elapsed time in dashboard
- **4.3** Progress bar and ETA based on subtask counts

### Phase 5: Result Merging & Conflict Detection
- **5.1** Git conflict detection tool
- **5.2** Structured build/lint/test verification
- **5.3** Auto-detect project linter

### Phase 6: End-to-End Efficiency
- **6.1** Acceleration ratio metric
- **6.2** Orchestration overhead metric
- **6.3** Token consumption tracking

### Phase 7: Memory System Enhancement
- **7.1** Age + relevance based eviction
- **7.2** Memory recall usefulness tracking
- **7.3** Error pattern auto-injection

### Phase 8: Three-Phase Workflow
- **8.1** Phase gating via `transition_phase` tool
- **8.2** Research phase with read-only constraint
- **8.3** Verify phase with mandatory checks

### Phase 9: User Interaction
- **9.1** Plan approval before implementation
- **9.2** Acceptance confirmation before completion

### Phase 10: Fault Tolerance
- **10.1** Failure detection and auto-retry (max 2)
- **10.2** Enhanced session resume with pane reconciliation

## Dependency Graph

```
✅ 1.1 Structured Decomposition
   +-- 1.2 Dependency Scheduling
   +-- ✅ 3.1 Context Injection
       +-- 3.2 Context Budget

✅ 4.1 Stuck Detection (independent)

✅ 5.1 Git Conflict Detection (independent)
✅ 5.2 Build Verification (independent)
   +-- 5.3 Code Consistency
   +-- 8.3 Verify Phase

✅ 6.3 Token Tracking (independent)
   +-- 6.1 Acceleration Ratio
   +-- 6.2 Orchestration Overhead

✅ 7.1 Memory Eviction (independent)
   +-- 7.2 Recall Usefulness
   +-- 7.3 Error Pattern Learning

✅ 8.1 Phase Gating (independent)
   +-- 8.2 Research Phase
   +-- 8.3 Verify Phase
   +-- 9.1 Plan Approval
       +-- 9.2 Acceptance Confirmation

✅ 10.1 Failure Auto-Retry (independent)
10.2 Resume Enhancement (independent)
```

## Dispatch Waves

- **Wave 1** (P0): ~~4.1 first, then 5.2 + 1.1 in parallel~~ **Complete** — all P0 items delivered
- **Wave 2** (P1): ~~7.1, 10.1, 6.3, 8.1 — all in parallel~~ **Complete** — all P1 items delivered
- **Wave 3**: 11.1 Branch Isolation, 11.2 Context Compression, 11.3 Cost Convergence, 2.1 Recommendation Accuracy

## Task Spec Format

Each spec in `docs/tasks/` follows a standard template. See [`_index.md`](tasks/_index.md) for the full index.
