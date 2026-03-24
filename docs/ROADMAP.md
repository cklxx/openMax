# openMax Roadmap

> **Strategic pivot**: Single-project parallelism has a ceiling (~1.5x) because one CC session is already efficient. The real value is **multi-project orchestration** — coordinating AI agents across multiple repos, automating per-project workflows, and managing cross-project dependencies. No single CC session can do this.

---

## Vision

**openMax = AI DevOps for multi-project engineering teams.**

One command dispatches agents across multiple projects simultaneously. Each project gets its own automated workflow (test, lint, deploy). The lead agent coordinates cross-project dependencies, resolves integration conflicts, and reports unified status.

```
openmax run "Upgrade auth library across all services"
    |
    +-- Project: user-service     → agent: bump dep, migrate API, test
    +-- Project: payment-service  → agent: bump dep, migrate API, test
    +-- Project: gateway          → agent: update routing, integration test
    +-- Cross-project: verify all services work together
```

---

## Completed (v0.1–v0.8)

| Phase | Items | Status |
|-------|-------|--------|
| Task Decomposition | structured `submit_plan`, dependency DAG, parallel groups | done |
| Agent Dispatch | branch isolation, role-based agent selection, worktrees | done |
| Context Passing | auto-inject file paths, blackboard, archetype hints | done |
| Execution Monitoring | stuck detection, mailbox signaling, auto-done detection | done |
| Result Merging | git merge with conflict resolution, auto-verify, auto-report | done |
| Efficiency | token tracking, cost anomaly detection, inline monitoring | done |
| Memory | age+relevance eviction, workspace memory | done |
| Workflow | phase gating, plan approval, failure auto-retry, session resume | done |
| Performance | auto-dispatch, parallel verify, adaptive polling, deferred cleanup | done |

---

## Phase Next: Multi-Project Orchestration

### N1. Project Registry (P0)
Define and manage a set of related projects.

- `openmax projects add <path>` — register a project directory
- `openmax projects list` — show registered projects with status (clean/dirty, branch, last run)
- Project config in `~/.openmax/projects.yaml`: path, default agents, auto-workflows
- Lead agent gets project registry as context for cross-project planning

### N2. Per-Project Automation (P0)
Each registered project can have automated workflows triggered by openMax.

- `openmax auto <project> --on push` — run lint+test on every push
- `openmax auto <project> --on pr` — auto-review PRs with reviewer agent
- Workflow definitions in project's `.openmax/workflows.yaml`:
  ```yaml
  on_push:
    - lint
    - test
  on_pr:
    - review
    - security-scan
  ```
- Agents dispatched per-project with isolated branches, just like today

### N3. Cross-Project Tasks (P0)
Dispatch a single task that spans multiple projects.

- Lead agent decomposes into per-project subtasks
- Each subtask dispatched to the correct project directory
- Cross-project dependencies tracked (e.g., "bump library in service A, then update service B")
- Unified merge verification across all affected projects
- Example: `openmax run "Add rate limiting to all API services" --projects api-gateway,user-service,billing-service`

### N4. Cross-Project Dependency Graph (P1)
Understand how projects relate to each other.

- Auto-detect shared dependencies (package.json, requirements.txt, go.mod)
- When upgrading a library, identify all affected projects
- Topological ordering for cross-project changes (upstream first)
- Conflict detection: "project A depends on v2 of lib X, project B still on v1"

### N5. Unified Dashboard (P1)
Multi-project status at a glance.

- One terminal view showing all projects and their agent status
- Per-project: branch, running agents, last result, health
- Cross-project: dependency conflicts, integration test status
- `openmax status` — quick summary across all projects

### N6. Integration Testing (P2)
After per-project changes merge, verify they work together.

- Auto-detect docker-compose, monorepo test suites, or custom integration scripts
- Dispatch integration test agent after all per-project agents complete
- Report cross-project compatibility status

### N7. Multi-Project Resume (P2)
Resume interrupted multi-project sessions.

- Session state includes per-project progress
- Resume picks up where each project left off
- Handle partial completion (3/5 projects done, resume remaining 2)

---

## Retained from v0.8 (Single-Project Improvements)

| Priority | ID | Title | Status |
|----------|----|-------|--------|
| P2 | 2.1 | Recommendation Accuracy | pending |
| P3 | 1.2 | Dependency Scheduling | pending |
| P3 | 5.3 | Code Consistency | pending |
| P3 | 10.2 | Resume Enhancement | pending |

---

## Dependency Graph

```
N1 Project Registry
 +-- N2 Per-Project Automation
 +-- N3 Cross-Project Tasks
     +-- N4 Cross-Project Dependency Graph
     +-- N6 Integration Testing
 +-- N5 Unified Dashboard
 +-- N7 Multi-Project Resume
```

## Implementation Waves

- **Wave 1 (P0)**: N1 Project Registry → N2 Per-Project Auto → N3 Cross-Project Tasks
- **Wave 2 (P1)**: N4 Dependency Graph + N5 Unified Dashboard
- **Wave 3 (P2)**: N6 Integration Testing + N7 Multi-Project Resume
