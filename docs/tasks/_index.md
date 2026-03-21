# openMax Task Index

> Each row is a dispatchable task spec. Status: `done` | `removed` | `pending`.

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

### Pillar A — Task Decomposition & Scheduling (→ Parallel Acceleration)

| ID | Title | Pri | Status |
|----|-------|-----|--------|
| 1.1 | Structured Decomposition | P0 | done |
| 1.2 | Dependency Scheduling (validation) | P3 | done |
| 2.2 | Load Balancing | P3 | pending |

### Pillar B — Context & Knowledge (→ Quality)

| ID | Title | Pri | Status |
|----|-------|-----|--------|
| 3.1 | Context Injection | P2 | done |
| 11.2 | Context Compression Protocol | P2 | done |
| 3.2 | Context Budget | P3 | done |
| 7.1 | Memory Eviction | P1 | removed (memory system deleted v0.5.37) |
| 2.1 | Recommendation Accuracy | P2 | removed (memory system deleted v0.5.37) |
| 7.2 | Recall Usefulness | P3 | removed (memory system deleted v0.5.37) |
| 7.3 | Error Pattern Learning | P3 | removed (memory system deleted v0.5.37) |

### Pillar C — Execution Reliability (→ Error Isolation)

| ID | Title | Pri | Status |
|----|-------|-----|--------|
| 4.1 | Stuck Detection | P0 | done |
| 10.1 | Failure Auto-Retry | P1 | done |
| 8.1 | Phase Gating | P1 | done |
| 11.1 | Branch Isolation & Auto-Merge | P1 | done |
| 8.2 | Research Phase | P3 | done (in prompt, not enforced) |
| 8.3 | Verify Phase | P3 | done |
| 9.1 | Plan Approval | P3 | done |
| 9.2 | Acceptance Confirmation | P3 | pending |
| 10.2 | Resume Enhancement | P3 | done (basic) |

### Pillar D — Quality Assurance (→ Adversarial Quality)

| ID | Title | Pri | Status |
|----|-------|-----|--------|
| 5.2 | Build Verification | P0 | done |
| 5.1 | Git Conflict Detection | P2 | done |
| 5.3 | Code Consistency (auto-linter) | P3 | done |

### Pillar E — Observability & Cost (→ Cost Controllability)

| ID | Title | Pri | Status |
|----|-------|-----|--------|
| 6.3 | Token Tracking | P1 | done |
| 11.3 | Cost Convergence & Budget Control | P2 | done |
| 4.2 | Dashboard Elapsed Time | P2 | done |
| 6.1 | Acceleration Ratio | P3 | done |
| 6.2 | Orchestration Overhead | P3 | done |
| 4.3 | Progress Readability | P3 | done |

---

## Progress Summary

| Status | Count |
|--------|-------|
| done | 22 |
| removed | 4 |
| pending | 3 |
| **total** | **29** |

### Remaining

| ID | Title | Pri | Notes |
|----|-------|-----|-------|
| 12.1 | Pane Early-Exit Diagnostics | P1 | Detect when agent pane exits immediately after creation (e.g. binary crash, config error). Capture exit output before pane disappears, report diagnostic to lead agent instead of blindly sending prompt to dead pane. |
| 12.2 | Textual TUI Dashboard | P1 | Interactive terminal UI with Textual: task list, DAG dependency view, live log stream, keyboard navigation. See `.openmax/briefs/tui-dashboard-implementation.md` |
| 2.2 | Load Balancing | P3 | No queue management or agent load distribution |
| 9.2 | Acceptance Confirmation | P3 | Post-verification user approval gate |

---

## Completed (chronological)

1. **4.1** Stuck Detection
2. **5.2** Build Verification
3. **1.1** Structured Decomposition
4. **7.1** Memory Eviction (later removed)
5. **10.1** Failure Auto-Retry
6. **6.3** Token Tracking
7. **8.1** Phase Gating
8. **3.1** Context Injection
9. **4.2** Dashboard Elapsed Time
10. **5.1** Git Conflict Detection
11. **11.1** Branch Isolation & Auto-Merge
12. **11.2** Context Compression Protocol
13. **11.3** Cost Convergence & Budget Control
14. **1.2** Dependency Scheduling (validation in submit_plan)
15. **8.2** Research Phase (prompt-driven)
16. **8.3** Verify Phase (run_verification tool)
17. **9.1** Plan Approval (interactive confirmation)
18. **10.2** Resume Enhancement (session reconciliation)
19. **3.2** Context Budget (context_budget_tokens param)
20. **6.1** Acceleration Ratio (scorecard)
21. **6.2** Orchestration Overhead (scorecard)
22. **4.3** Progress Readability (RunDashboard)
23. **5.3** Code Consistency (project_tools.py auto-detection)
