"""Tests for dashboard visual improvements."""

from __future__ import annotations

import io
import time

from rich.console import Console
from rich.panel import Panel

from openmax.dashboard import (
    RunDashboard,
    SessionMetrics,
    _format_duration,
    _format_tokens,
    _max_concurrent,
    _render_progress_bar,
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
    """Token counts and cost appear in done banner when set."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.set_session_metrics(total_input_tokens=45200, total_output_tokens=12800)

    output = _render_to_string(dash)
    assert "58.0k tokens" in output
    assert "$" in output
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


def test_format_duration_hours():
    assert _format_duration(3900) == "1h 05m"


def test_format_duration_negative():
    assert _format_duration(-5) == "0s"


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


# ── Layer 1: activity column ─────────────────────────────────────


def test_task_activity_shows_pane_output():
    """Running task shows pane_activity content in rendered output."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_subtask("fix-auth", "claude", 1, "running", started_at=time.time())
    dash.update_pane_activity(1, "Running tests...")

    output = _render_to_string(dash)
    assert "Running tests" in output
    dash.stop()


def test_default_mode_no_pane_id():
    """Default mode does not show pane_id column values."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_subtask("fix-auth", "claude", 42, "running", started_at=time.time())

    output = _render_to_string(dash)
    assert "#42" not in output
    dash.stop()


def test_verbose_mode_shows_pane_id():
    """Verbose mode includes pane_id in the output."""
    dash = RunDashboard("test goal", verbose=True)
    dash.start()
    dash.update_subtask("fix-auth", "claude", 42, "running", started_at=time.time())

    output = _render_to_string(dash)
    assert "#42" in output
    dash.stop()


def test_verbose_mode_shows_dispatch_prompt():
    """Verbose mode shows the dispatch prompt first line."""
    dash = RunDashboard("test goal", verbose=True)
    dash.start()
    dash.update_subtask("fix-auth", "claude", 1, "running", started_at=time.time())
    dash.set_dispatch_prompt("fix-auth", "Implement auth fix\nMore details here")

    output = _render_to_string(dash)
    assert "Implement auth fix" in output
    dash.stop()


def test_set_dispatch_prompt_stores_first_line():
    """set_dispatch_prompt extracts only the first line."""
    dash = RunDashboard("test goal")
    dash.set_dispatch_prompt("task-a", "First line\nSecond line\nThird line")
    assert dash.dispatch_prompts["task-a"] == "First line"


# ── ETA estimation ───────────────────────────────────────────────


def test_eta_returns_none_when_no_tasks():
    """ETA is None when there are no tasks."""
    dash = RunDashboard("test goal")
    assert dash._estimate_eta(0, 0) is None


def test_eta_returns_none_below_threshold():
    """ETA is None when less than 10% complete."""
    dash = RunDashboard("test goal")
    dash.start_time = time.monotonic() - 10
    assert dash._estimate_eta(0, 10) is None


def test_eta_returns_value_above_threshold():
    """ETA returns positive seconds when >10% done."""
    dash = RunDashboard("test goal")
    dash.start_time = time.monotonic() - 100
    eta = dash._estimate_eta(5, 10)
    assert eta is not None
    assert eta > 0


def test_eta_none_when_all_done():
    """ETA is None when all tasks complete."""
    dash = RunDashboard("test goal")
    dash.start_time = time.monotonic() - 60
    assert dash._estimate_eta(5, 5) is None


# ── Progress bar with ETA ────────────────────────────────────────


def test_progress_shows_eta_when_enough_done():
    """ETA appears in progress line when enough tasks are done."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.start_time = time.monotonic() - 120
    now = time.time()
    for i in range(3):
        dash.update_subtask(f"done-{i}", "claude", i, "done", started_at=now, finished_at=now)
    for i in range(7):
        dash.update_subtask(f"run-{i}", "claude", 10 + i, "running", started_at=now)
    output = _render_to_string(dash)
    assert "ETA" in output
    dash.stop()


# ── Real-time cost in progress line ──────────────────────────────


def test_progress_line_shows_cost_when_tokens_set():
    """Progress line shows token count and cost during execution."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "running", started_at=now)
    dash.set_session_metrics(total_input_tokens=10000, total_output_tokens=5000)

    output = _render_to_string(dash)
    assert "tokens" in output
    assert "$" in output
    dash.stop()


def test_progress_line_no_cost_when_zero_tokens():
    """No token/cost info in progress line when tokens are zero."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "running", started_at=now)

    output = _render_to_string(dash)
    assert "$" not in output
    dash.stop()


# ── Done banner panel ────────────────────────────────────────────


def test_done_banner_is_panel():
    """Done banner renders as a Panel."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    banner = dash._render_done_banner()
    assert isinstance(banner, Panel)
    dash.stop()


def test_done_banner_yellow_on_errors():
    """Done banner shows yellow style when errors exist."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.update_subtask("t2", "claude", 2, "error", started_at=now, finished_at=now)
    output = _render_to_string(dash)
    assert "ALL DONE" in output
    assert "1 error" in output
    dash.stop()


def test_done_banner_shows_total_tokens():
    """Done banner shows combined token count."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.set_session_metrics(total_input_tokens=100_000, total_output_tokens=25_000)
    output = _render_to_string(dash)
    assert "125.0k tokens" in output
    dash.stop()


def test_done_banner_task_count():
    """Done banner shows task completion ratio."""
    dash = RunDashboard("test goal")
    dash.start()
    now = time.time()
    dash.update_subtask("t1", "claude", 1, "done", started_at=now, finished_at=now)
    dash.update_subtask("t2", "claude", 2, "done", started_at=now, finished_at=now)
    output = _render_to_string(dash)
    assert "2/2 tasks" in output
    dash.stop()


# ── Progress bar helper ───────────────────────────────────────────


def test_progress_bar_done():
    assert _render_progress_bar(50, "done") == "\u2714"


def test_progress_bar_error():
    assert _render_progress_bar(50, "error") == "\u2718"


def test_progress_bar_running_no_pct():
    assert _render_progress_bar(None, "running") == "[\u00b7\u00b7\u00b7]"


def test_progress_bar_pending_no_pct():
    assert _render_progress_bar(None, "pending") == ""


def test_progress_bar_half():
    result = _render_progress_bar(50, "running")
    assert "50%" in result
    assert "\u2588" in result
    assert "\u2591" in result


def test_progress_bar_full():
    result = _render_progress_bar(100, "running")
    assert "100%" in result


def test_progress_bar_clamps_negative():
    result = _render_progress_bar(-10, "running")
    assert "0%" in result


def test_progress_bar_clamps_over_100():
    result = _render_progress_bar(200, "running")
    assert "100%" in result


# ── Per-task progress in dashboard rendering ──────────────────────


def test_task_progress_rendered_in_table():
    """Task progress bar appears in the rendered subtask table."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_subtask("fix-auth", "claude", 1, "running", started_at=time.time())
    dash.update_task_progress("fix-auth", 52)

    output = _render_to_string(dash)
    assert "52%" in output
    dash.stop()


def test_task_progress_indeterminate_when_missing():
    """Running task with no progress shows indeterminate indicator."""
    dash = RunDashboard("test goal")
    dash.start()
    dash.update_subtask("fix-auth", "claude", 1, "running", started_at=time.time())

    output = _render_to_string(dash)
    assert "[\u00b7\u00b7\u00b7]" in output
    dash.stop()


def test_task_progress_clamps_values():
    """Values outside 0-100 are clamped."""
    dash = RunDashboard("test goal")
    dash.update_task_progress("t1", -5)
    assert dash.task_progress["t1"] == 0
    dash.update_task_progress("t1", 150)
    assert dash.task_progress["t1"] == 100
