"""E2E tests using the real Claude API.

Guarded by OPENMAX_E2E=1 — these tests hit Claude's API and take time.
Run with: OPENMAX_E2E=1 pytest tests/test_e2e.py -v -s --timeout=300
"""

from __future__ import annotations

import os

import pytest

from openmax.adapters import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, AgentRegistry
from openmax.lead_agent.core import run_lead_agent
from openmax.loop_session import LoopIteration, LoopSessionStore, build_loop_context
from openmax.pane_backend import HeadlessPaneBackend
from openmax.pane_manager import PaneManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENMAX_E2E"),
    reason="Set OPENMAX_E2E=1 to run real E2E tests",
)

# ── Sub-agent fixture ──────────────────────────────────────────────────────────

# Non-interactive script: writes the expected .openmax/reports/<task>.md report file
# (so the lead agent sees a proper completion signal), writes a test marker, and exits.
# Receives cwd as sys.argv[1] via the {cwd} template substitution.
_TASK_AGENT_SCRIPT = """\
import os, pathlib, sys
cwd = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
m = os.environ.get("OPENMAX_E2E_MARKER", "")
if m:
    pathlib.Path(m).write_text("done")
reports = cwd / ".openmax" / "reports"
reports.mkdir(parents=True, exist_ok=True)
briefs = cwd / ".openmax" / "briefs"
if briefs.exists():
    for brief in briefs.glob("*.md"):
        (reports / brief.name).write_text(
            "## Status\\ndone\\n\\n## Summary\\nAll checks passed.\\n"
            "\\n## Changes\\n- None\\n\\n## Test Results\\nPASS\\n"
        )
print("Task completed successfully. All work is done.")
"""


def _e2e_registry(marker_path: str) -> AgentRegistry:
    adapter = SubprocessAdapter(
        name="task-agent",
        command_template=["python3", "-c", _TASK_AGENT_SCRIPT, "{cwd}"],
        is_interactive=False,
        env={"OPENMAX_E2E_MARKER": marker_path},
    )
    return AgentRegistry([AgentDefinition(name="task-agent", adapter=adapter, source="e2e-test")])


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_e2e_lead_agent_dispatches_and_reports_completion(tmp_path):
    """Lead agent dispatches task-agent, reads output, and reports at least one done subtask."""
    marker = str(tmp_path / "done.marker")
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())
    task = "Run a quick health check on the repository using task-agent and report the results."
    result = run_lead_agent(
        task=task,
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["task-agent"],
        agent_registry=_e2e_registry(marker),
        max_turns=15,
    )

    done_count = sum(1 for t in result.subtasks if t.status.value == "done")
    assert done_count >= 1, f"Expected ≥1 done subtask; got: {result.subtasks}"
    assert os.path.exists(marker), "Sub-agent marker file was not written — task-agent never ran"


def test_e2e_loop_tape_and_context_injection(tmp_path):
    """Two-iteration loop: tape accumulates entries; iter 2 receives prior-context warning."""
    import openmax.loop_session as mod

    # Redirect loop store to tmp_path
    original_loops_dir = mod._loops_dir
    loops_dir = tmp_path / "loops"
    loops_dir.mkdir()
    mod._loops_dir = lambda: loops_dir  # type: ignore[attr-defined]

    try:
        store = LoopSessionStore()
        loop_session = store.create(goal="Run a health check on the codebase", cwd=str(tmp_path))
        task = "Run a quick health check on the repository using task-agent."

        # ── Iteration 1 ────────────────────────────────────────────────────────
        loop_ctx_1 = build_loop_context(loop_session, current_iteration=1)
        assert loop_ctx_1 == "", "First iteration should have no prior context"

        marker1 = str(tmp_path / "iter1.marker")
        result1 = run_lead_agent(
            task=task,
            pane_mgr=PaneManager(backend=HeadlessPaneBackend()),
            cwd=str(tmp_path),
            allowed_agents=["task-agent"],
            agent_registry=_e2e_registry(marker1),
            max_turns=12,
        )

        # Record to tape
        done1 = [t.name for t in result1.subtasks if t.status.value == "done"]
        failed1 = [t.name for t in result1.subtasks if t.status.value == "error"]
        total1 = len(result1.subtasks)
        iter1 = LoopIteration(
            iteration=1,
            session_id=None,
            started_at="2026-03-18T10:00:00+00:00",
            completed_at="2026-03-18T10:01:00+00:00",
            outcome_summary=f"{len(done1)}/{total1} subtasks done",
            completion_pct=int(len(done1) / total1 * 100) if total1 else 100,
            tasks_done=done1,
            tasks_failed=failed1,
        )
        store.append_iteration(loop_session.loop_id, iter1)
        loop_session.iterations.append(iter1)

        assert os.path.exists(marker1), "task-agent did not run in iteration 1"

        # Tape should have 1 entry
        loaded = store.load(loop_session.loop_id)
        assert loaded is not None
        assert len(loaded.iterations) == 1

        # ── Verify loop context for iteration 2 ────────────────────────────────
        loop_ctx_2 = build_loop_context(loop_session, current_iteration=2)
        assert "DO NOT repeat" in loop_ctx_2, "Loop context missing DO NOT repeat warning"
        assert "Iteration 2" in loop_ctx_2, "Loop context missing current iteration header"

        # ── Iteration 2 — with prior-context injected ──────────────────────────
        marker2 = str(tmp_path / "iter2.marker")
        result2 = run_lead_agent(
            task=task,
            pane_mgr=PaneManager(backend=HeadlessPaneBackend()),
            cwd=str(tmp_path),
            allowed_agents=["task-agent"],
            agent_registry=_e2e_registry(marker2),
            max_turns=12,
            loop_context=loop_ctx_2,
        )

        done2 = [t.name for t in result2.subtasks if t.status.value == "done"]
        failed2 = [t.name for t in result2.subtasks if t.status.value == "error"]
        total2 = len(result2.subtasks)
        iter2 = LoopIteration(
            iteration=2,
            session_id=None,
            started_at="2026-03-18T10:01:30+00:00",
            completed_at="2026-03-18T10:02:30+00:00",
            outcome_summary=f"{len(done2)}/{total2} subtasks done",
            completion_pct=int(len(done2) / total2 * 100) if total2 else 100,
            tasks_done=done2,
            tasks_failed=failed2,
        )
        store.append_iteration(loop_session.loop_id, iter2)

        assert os.path.exists(marker2), "task-agent did not run in iteration 2"

        # Tape should have 2 entries
        loaded2 = store.load(loop_session.loop_id)
        assert loaded2 is not None
        assert len(loaded2.iterations) == 2
        assert loaded2.iterations[0].iteration == 1
        assert loaded2.iterations[1].iteration == 2

    finally:
        mod._loops_dir = original_loops_dir
