"""Tests for dashboard visual improvements."""

from __future__ import annotations

import io
import time

from rich.console import Console

from openmax.dashboard import (
    RunDashboard,
    SessionMetrics,
    _format_duration,
    _format_tokens,
    _max_concurrent,
    _truncate,
    render_session_summary,
)


def _render_to_string(dashboard: RunDashboard) -> str:
    """Render the dashboard to a plain string for assertion."""
    renderable = dashboard._render()
    buf = io.StringIO()
    c = Console(file=buf, width=100, force_terminal=False, no_color=True)
    c.print(renderable, end="")
    return buf.getvalue()


def _render_panel_to_string(panel) -> str:
    buf = io.StringIO()
    c = Console(file=buf, width=80, force_terminal=False, no_color=True)
    c.print(panel, end="")
    return buf.getvalue()


# ── Done banner ──────────────────────────────────────────────────


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
    _, research_end = dash.phase_times["research"]
    assert research_end is not None
    _, implement_end = dash.phase_times["implement"]
    assert implement_end is None
    dash.stop()


# ── Acceleration & metrics in done banner ────────────────────────


def test_done_banner_shows_acceleration():
    """Acceleration ratio appears in the done banner when set."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.set_session_metrics(acceleration_ratio=3.2)

    output = _render_to_string(dash)
    assert "3.2x" in output
    dash.stop()


def test_done_banner_shows_tokens():
    """Token counts appear in done banner when set."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.set_session_metrics(total_input_tokens=45200, total_output_tokens=12800)

    output = _render_to_string(dash)
    assert "45.2k" in output
    assert "12.8k" in output
    dash.stop()


def test_done_banner_shows_time_saved():
    """Time saved line appears when estimated minutes are provided."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask(
        "t1",
        "claude",
        1,
        "done",
        started_at=now,
        finished_at=now,
        estimated_minutes=5,
    )
    dash.update_subtask(
        "t2",
        "codex",
        2,
        "done",
        started_at=now,
        finished_at=now,
        estimated_minutes=3,
    )

    output = _render_to_string(dash)
    assert "saved" in output
    assert "8m est" in output
    dash.stop()


def test_done_banner_no_metrics_when_empty():
    """No extra metric lines when session metrics are not populated."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)

    output = _render_to_string(dash)
    assert "ALL DONE" in output
    assert "tokens" not in output
    assert "saved" not in output
    dash.stop()


# ── Estimated minutes in subtask updates ─────────────────────────


def test_estimated_minutes_stored():
    """estimated_minutes passed to update_subtask is stored in metrics."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_subtask("fix-auth", "claude", 1, "running", estimated_minutes=5)

    assert dash.metrics.estimated_human_minutes["fix-auth"] == 5
    dash.stop()


# ── Helper functions ─────────────────────────────────────────────


def test_format_duration_seconds():
    assert _format_duration(45) == "45s"


def test_format_duration_minutes():
    assert _format_duration(125) == "2m 05s"


def test_format_tokens_small():
    assert _format_tokens(500) == "500"


def test_format_tokens_thousands():
    assert _format_tokens(45200) == "45.2k"


def test_format_tokens_millions():
    assert _format_tokens(1_500_000) == "1.5M"


def test_truncate_short():
    assert _truncate("hello", 10) == "hello"


def test_truncate_long():
    result = _truncate("a-very-long-task-name-here", 10)
    assert len(result) == 10
    assert result.endswith("\u2026")


# ── Max concurrent calculation ───────────────────────────────────


def test_max_concurrent_sequential():
    """Tasks with no overlap have peak concurrency 1."""
    now = time.monotonic()
    subtasks = {
        "a": {"started_at": now, "finished_at": now + 10},
        "b": {"started_at": now + 15, "finished_at": now + 25},
    }
    assert _max_concurrent(subtasks) == 1


def test_max_concurrent_parallel():
    """Overlapping tasks show higher concurrency."""
    now = time.monotonic()
    subtasks = {
        "a": {"started_at": now, "finished_at": now + 20},
        "b": {"started_at": now + 5, "finished_at": now + 15},
        "c": {"started_at": now + 10, "finished_at": now + 25},
    }
    assert _max_concurrent(subtasks) >= 2


# ── render_session_summary (standalone) ──────────────────────────


def test_render_session_summary_basic():
    """render_session_summary produces a panel with task breakdown."""
    now = time.monotonic()
    subtasks = {
        "fix-auth": {
            "agent": "claude",
            "status": "done",
            "pane_id": 1,
            "started_at": now,
            "finished_at": now + 60,
        },
        "add-tests": {
            "agent": "codex",
            "status": "error",
            "pane_id": 2,
            "started_at": now,
            "finished_at": now + 45,
        },
    }
    metrics = SessionMetrics(
        total_input_tokens=10000,
        total_output_tokens=5000,
        acceleration_ratio=2.5,
        estimated_human_minutes={"fix-auth": 5, "add-tests": 3},
    )
    panel = render_session_summary(subtasks, metrics, wall_seconds=105)
    output = _render_panel_to_string(panel)

    assert "Session Summary" in output
    assert "2.5x" in output
    assert "1/2 succeeded" in output
    assert "1 error" in output
    assert "10.0k" in output


def test_render_session_summary_no_tasks():
    """Empty subtasks still renders without errors."""
    metrics = SessionMetrics()
    panel = render_session_summary({}, metrics, wall_seconds=0)
    output = _render_panel_to_string(panel)
    assert "Session Summary" in output
