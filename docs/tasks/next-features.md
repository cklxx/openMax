# Next Features: Session History & Automated Workflows

Two high-priority features to improve operational visibility and team automation.
Session History surfaces past run data through the CLI; Automated Workflows lets
teams define repeatable openMax pipelines triggered by git events.

| Feature | Priority | Dependencies | Status |
|---------|----------|--------------|--------|
| Session History (`openmax history`) | P1 | — | pending |
| Automated Workflows (`.openmax/workflows.yaml`) | P1 | — | pending |

---

## Feature 1: Session History (`openmax history`)

| Field | Value |
|-------|-------|
| **Priority** | P1 |
| **Dependencies** | — |
| **Status** | pending |

### Goal

A new CLI subcommand `openmax history` that displays past sessions in a rich
table with task names, results, duration, and cost. Reuses the existing
`SessionStore` and `UsageStore` — no new storage layer needed.

### Key Files

- `src/openmax/cli.py` — add `history` command to the Sessions group
- `src/openmax/session_runtime.py` — `SessionStore.list_sessions()`, `SessionMeta`
- `src/openmax/usage.py` — `UsageStore.load()`, `UsageStore.aggregate()`, `SessionUsage`

### Acceptance Criteria

- [ ] `openmax history` prints a Rich table with columns: Date, Session ID (8-char truncated), Task (40-char truncated), Status, Duration, Cost, Completion%
- [ ] `--limit N` flag (default 20) controls how many sessions are shown
- [ ] `--json` flag outputs machine-readable JSON array
- [ ] `--filter STATUS` flag filters by session status (e.g. `done`, `error`, `active`)
- [ ] Summary row at the bottom shows aggregate totals (total cost, total duration, session count)
- [ ] Command appears in the "Sessions" group in `openmax --help`
- [ ] Works gracefully with zero sessions (prints "No sessions found.")
- [ ] Tests cover: empty store, multiple sessions, `--limit`, `--json`, `--filter`, summary row

### Implementation Notes

**CLI wiring (cli.py):**

1. Add `history` to `GroupedGroup.command_groups` under "Sessions":
   ```python
   ("Sessions", ["sessions", "inspect", "usage", "log", "history"]),
   ```
2. Define the command:
   ```python
   @cli.command()
   @click.option("--limit", "-n", default=20, help="Max sessions to show")
   @click.option("--json", "as_json", is_flag=True, help="JSON output")
   @click.option("--filter", "status_filter", help="Filter by status")
   def history(limit: int, as_json: bool, status_filter: str | None) -> None:
   ```

**Data joining:**

- Call `SessionStore().list_sessions()` to get `list[SessionMeta]` (sorted by `updated_at` DESC)
- For each `SessionMeta`, call `UsageStore().load(meta.session_id)` to get cost/duration/tokens
- Optionally load `SessionStore().load_snapshot(sid)` to get `RunScorecard.completion_pct`
- Apply `--filter` on `meta.status` before building the table
- Apply `--limit` after filtering

**Table rendering:**

- Use `rich.table.Table` (already imported in `cli.py`) via the shared `console` from `openmax.output`
- Date column: format `meta.created_at` with `format_relative_time()` from `openmax.formatting`
- Status column: use `status_icon()` from `openmax.formatting` for colored status
- Duration column: `usage.format_duration()` if usage exists, else "—"
- Cost column: `usage.format_cost()` if usage exists, else "—"
- Completion column: from `RunScorecard.completion_pct` if snapshot loaded, else "—"

**Summary row:**

- Collect all `SessionUsage` records that matched the filter
- Call `UsageStore().aggregate(matched_records)` for totals
- Render as a footer row: `f"{count} sessions | {agg.format_cost()} | {agg.format_duration()}"`

**JSON output:**

- Build a list of dicts with keys: `session_id`, `task`, `status`, `created_at`, `duration_ms`, `cost_usd`, `completion_pct`
- Print via `click.echo(json.dumps(records, indent=2))`

**No new dependencies required.** Rich, Click, SessionStore, and UsageStore are all available.

### Dispatch Strategy

- Agent count: 1
- Prompt key points: "Add `history` command to `src/openmax/cli.py` in the Sessions group. Join `SessionStore.list_sessions()` with `UsageStore.load()` per session. Rich table output with Date, Session ID, Task, Status, Duration, Cost, Completion%. Support `--limit`, `--json`, `--filter` flags. Summary row via `UsageStore.aggregate()`. Add tests in `tests/test_history_command.py`. Follow the spec in `docs/tasks/next-features.md` Feature 1. Run `ruff check` and `pytest` before committing."

---

## Feature 2: Automated Workflows (`.openmax/workflows.yaml`)

| Field | Value |
|-------|-------|
| **Priority** | P1 |
| **Dependencies** | — |
| **Status** | pending |

### Goal

Per-project `.openmax/workflows.yaml` files that define automated openMax
workflows tied to git events. Allows teams to codify repeatable pipelines
like lint+test on push or code review on PR.

### Key Files

- **NEW:** `src/openmax/workflows.py` — core logic: YAML parsing, validation, workflow execution
- **NEW:** `src/openmax/workflow_schema.py` — dataclasses: `WorkflowConfig`, `WorkflowStep`, `Workflow`, `TriggerType`
- **MODIFY:** `src/openmax/cli.py` — add `workflow` command group with `list`, `run`, `validate`, `init` subcommands
- **NEW:** `templates/github-workflow.yaml.j2` — Jinja2 template for GitHub Actions integration
- **MODIFY:** `pyproject.toml` — add `PyYAML` dependency

### Acceptance Criteria

- [ ] `.openmax/workflows.yaml` is parsed and validated with clear error messages
- [ ] `openmax workflow list` shows all configured workflows with their triggers
- [ ] `openmax workflow run <name>` manually triggers a named workflow
- [ ] `openmax workflow validate` checks YAML syntax and schema, reports errors
- [ ] `openmax workflow init` generates git hooks (`pre-push`) and optionally a GitHub Actions workflow file
- [ ] Two step types supported: `run` (shell command) and `openmax` (dispatches a session)
- [ ] Four trigger types recognized: `on_push`, `on_pr`, `on_commit`, `manual`
- [ ] Step failures propagate correctly (fail-fast by default)
- [ ] Tests cover: YAML parsing (valid/invalid), validation, step execution (mocked), trigger matching, CLI subcommands

### Implementation Notes

**YAML schema (`workflow_schema.py`):**

```python
from dataclasses import dataclass, field
from enum import Enum

class TriggerType(Enum):
    ON_PUSH = "on_push"
    ON_PR = "on_pr"
    ON_COMMIT = "on_commit"
    MANUAL = "manual"

@dataclass
class ShellStep:
    run: str

@dataclass
class OpenMaxStep:
    openmax: str
    role: str = "default"

WorkflowStep = ShellStep | OpenMaxStep

@dataclass
class Workflow:
    name: str
    triggers: list[TriggerType]
    steps: list[WorkflowStep]

@dataclass
class WorkflowConfig:
    version: int
    workflows: list[Workflow]
```

**Parsing and validation (`workflows.py`):**

1. `load_workflow_config(path: Path) -> WorkflowConfig` — reads YAML, validates schema version, converts to dataclasses. Raises `WorkflowConfigError` on invalid input.
2. `validate_config(config: WorkflowConfig) -> list[str]` — returns list of validation warnings (duplicate names, empty steps, unknown triggers).
3. `find_workflow_file(cwd: Path) -> Path | None` — walks up from `cwd` looking for `.openmax/workflows.yaml`.

**Workflow execution (`workflows.py`):**

1. `run_workflow(workflow: Workflow, cwd: Path) -> bool` — executes steps sequentially:
   - `ShellStep`: run via `subprocess.run(step.run, shell=True, cwd=cwd)`, check returncode
   - `OpenMaxStep`: invoke `openmax run` as subprocess with the prompt and role
2. Return `True` if all steps pass, `False` on first failure (fail-fast)
3. Print step progress via Rich console output

**CLI commands (`cli.py`):**

Add a `workflow` group to `GroupedGroup.command_groups` under a new "Workflows" group:
```python
("Workflows", ["workflow"]),
```

Subcommands via `@click.group()`:
- `workflow list` — load config, print table of workflows with name, triggers, step count
- `workflow run <name>` — find workflow by name, execute via `run_workflow()`
- `workflow validate` — load config, run `validate_config()`, print results
- `workflow init` — generate `.git/hooks/pre-push` that calls `openmax workflow run` for `on_push` triggers; optionally write `.github/workflows/openmax.yml` from Jinja2 template

**Git hook generation (`workflow init`):**

- Pre-push hook: iterate workflows with `on_push` trigger, generate shell script calling `openmax workflow run <name>` for each
- GitHub Actions template (`templates/github-workflow.yaml.j2`): a workflow that runs on `push`/`pull_request` events and invokes `openmax workflow run` for matching triggers
- Use `pkg_resources` or `importlib.resources` to locate the template

**Example `.openmax/workflows.yaml`:**

```yaml
version: 1
workflows:
  lint-and-test:
    triggers: [on_push]
    steps:
      - run: "ruff check . && ruff format --check ."
      - run: "pytest tests/ -v"
  review:
    triggers: [on_pr]
    steps:
      - openmax: "Review this PR for bugs, security issues, and style violations"
        role: reviewer
  deploy-check:
    triggers: [manual]
    steps:
      - run: "python -m build"
      - openmax: "Verify the build artifacts are correct and complete"
        role: verifier
```

**Dependencies:**

- Add `PyYAML >= 6.0` to `pyproject.toml` under `[project.dependencies]`
- Jinja2 is only needed for `workflow init` — consider making it optional or using string templating

**Cross-reference:** Workflows that use `openmax` steps will generate sessions visible in `openmax history` (Feature 1), providing end-to-end traceability from workflow trigger to session cost.

### Dispatch Strategy

- Agent count: 2 (one for schema + core logic, one for CLI + templates)
- Prompt key points:
  - Agent 1: "Create `src/openmax/workflow_schema.py` with dataclasses and `src/openmax/workflows.py` with YAML parsing, validation, and execution logic. Add PyYAML to `pyproject.toml`. Add unit tests in `tests/test_workflows.py`. Follow the spec in `docs/tasks/next-features.md` Feature 2."
  - Agent 2: "Add `workflow` command group to `src/openmax/cli.py` with `list`, `run`, `validate`, `init` subcommands. Create `templates/github-workflow.yaml.j2`. Add CLI tests in `tests/test_workflows.py`. Follow the spec in `docs/tasks/next-features.md` Feature 2. Run `ruff check` and `pytest` before committing."
