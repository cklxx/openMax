"""Tests for dashboard visual improvements (Task 4.3)."""

from __future__ import annotations

import io
import time

from rich.console import Console

from openmax.dashboard import RunDashboard


def _render_to_string(dashboard: RunDashboard) -> str:
    """Render the dashboard to a plain string for assertion."""
    renderable = dashboard._render()
    buf = io.StringIO()
    c = Console(file=buf, width=100, force_terminal=False, no_color=True)
    c.print(renderable, end="")
    return buf.getvalue()


def test_dashboard_all_done_signal():
    """When all subtasks are done, output contains a done indicator."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("task-a", "codex", 1, "done", started_at=now, finished_at=now)
    dash.update_subtask("task-b", "codex", 2, "done", started_at=now, finished_at=now)

    output = _render_to_string(dash)
    assert "ALL DONE" in output
    dash.stop()


def test_dashboard_no_done_signal_when_tasks_pending():
    """When tasks are still running, no done banner appears."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("task-a", "codex", 1, "done", started_at=now, finished_at=now)
    dash.update_subtask("task-b", "codex", 2, "running", started_at=time.time())

    output = _render_to_string(dash)
    assert "ALL DONE" not in output
    dash.stop()


def test_dashboard_phase_duration_tracking():
    """update_phase populates phase_times with start timestamps."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_phase("research")
    dash.update_phase("implement")

    assert "research" in dash.phase_times
    assert "implement" in dash.phase_times
    # research should have been closed (end != None)
    _, research_end = dash.phase_times["research"]
    assert research_end is not None
    # implement is still open
    _, implement_end = dash.phase_times["implement"]
    assert implement_end is None
    dash.stop()
