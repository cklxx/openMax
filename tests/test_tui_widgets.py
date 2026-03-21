"""Tests for openmax.tui.widgets — unit + Textual app integration."""

from __future__ import annotations

import time

import pytest

from openmax.tui.bridge import DashboardBridge, DashboardState, SubtaskInfo  # noqa: F401
from openmax.tui.widgets import (
    DagScreen,
    HelpScreen,
    LogViewerWidget,
    StatusBarWidget,
    TaskListWidget,
)
from openmax.tui.widgets.task_list import _elapsed_str, _progress_bar, _task_icon

# -- Unit tests for helpers --


def test_task_icon_known_statuses():
    assert _task_icon("done") == "\u2713"
    assert _task_icon("running") == "\u25cf"
    assert _task_icon("pending") == "\u25cb"
    assert _task_icon("error") == "\u2717"


def test_task_icon_unknown_status():
    assert _task_icon("unknown") == "?"


def test_task_icon_accessible_mode(monkeypatch):
    """Accessible mode appends text labels to icons."""
    monkeypatch.setenv("OPENMAX_ACCESSIBLE", "1")
    assert _task_icon("running") == "\u25cf RUN"
    assert _task_icon("done") == "\u2713 DONE"
    assert _task_icon("error") == "\u2717 FAIL"
    assert _task_icon("pending") == "\u25cb WAIT"


def test_task_icon_compact_mode(monkeypatch):
    """Compact mode (default) returns icon only."""
    monkeypatch.delenv("OPENMAX_ACCESSIBLE", raising=False)
    assert _task_icon("running") == "\u25cf"


def test_elapsed_str_no_start():
    assert _elapsed_str(None, None) == "--"


def test_elapsed_str_with_start_and_finish():
    result = _elapsed_str(100.0, 112.5)
    assert result == "12s"


def test_elapsed_str_running():
    started = time.monotonic() - 5.0
    result = _elapsed_str(started, None)
    assert result.endswith("s")
    val = int(result.rstrip("s"))
    assert 4 <= val <= 7


def test_progress_bar_running_with_pct():
    result = _progress_bar(52, "running")
    assert "52%" in result
    assert "\u2588" in result


def test_progress_bar_done_shows_check():
    assert _progress_bar(100, "done") == "\u2714"


def test_progress_bar_no_pct_running():
    assert _progress_bar(None, "running") == "[\u00b7\u00b7\u00b7]"


def test_progress_bar_no_pct_pending():
    assert _progress_bar(None, "pending") == ""


# -- Widget refresh_from_state tests (unit, no Textual app) --


def _make_state(**overrides) -> DashboardState:
    defaults = {"goal": "test"}
    defaults.update(overrides)
    return DashboardState(**defaults)


def _two_task_state() -> DashboardState:
    return _make_state(
        subtasks={
            "auth": SubtaskInfo(
                name="auth",
                agent="claude",
                pane_id=1,
                status="running",
                started_at=100.0,
            ),
            "api": SubtaskInfo(
                name="api",
                agent="codex",
                pane_id=2,
                status="done",
                started_at=100.0,
                finished_at=110.0,
            ),
        }
    )


class TestTaskListWidget:
    def test_refresh_empty(self):
        w = TaskListWidget()
        state = _make_state()
        w.refresh_from_state(state)

    def test_refresh_with_tasks(self):
        state = _make_state(
            subtasks={
                "auth": SubtaskInfo(
                    name="auth",
                    agent="claude",
                    pane_id=1,
                    status="running",
                    started_at=100.0,
                ),
                "api": SubtaskInfo(
                    name="api",
                    agent="codex",
                    pane_id=2,
                    status="done",
                    started_at=100.0,
                    finished_at=110.0,
                ),
            },
            task_progress={"auth": 42},
        )
        w = TaskListWidget()
        w.refresh_from_state(_two_task_state())

    def test_selected_task_none_when_empty(self):
        w = TaskListWidget()
        w.refresh_from_state(_make_state())
        assert w.selected_task is None

    def test_selected_task_returns_first_by_default(self):
        w = TaskListWidget()
        w.refresh_from_state(_two_task_state())
        assert w.selected_task == "auth"

    def test_move_cursor_changes_selection(self):
        w = TaskListWidget()
        w.refresh_from_state(_two_task_state())
        w.move_cursor(1)
        assert w.selected_task == "api"

    def test_move_cursor_clamps_at_bounds(self):
        w = TaskListWidget()
        w.refresh_from_state(_two_task_state())
        w.move_cursor(-5)
        assert w.selected_task == "auth"
        w.move_cursor(100)
        assert w.selected_task == "api"

    def test_task_status_lookup(self):
        w = TaskListWidget()
        w.refresh_from_state(_two_task_state())
        assert w.task_status("auth") == "running"
        assert w.task_status("api") == "done"
        assert w.task_status("nonexistent") is None


class TestLogViewerWidget:
    def test_refresh_empty(self):
        w = LogViewerWidget()
        w.refresh_from_state(_make_state())

    def test_refresh_with_events(self):
        events = [
            {"text": "Starting task", "category": "system", "ts": 1.0},
            {"text": "Task complete", "category": "system", "ts": 2.0},
        ]
        w = LogViewerWidget()
        w.refresh_from_state(_make_state(tool_events=events))

    def test_filter_by_task(self):
        events = [
            {"text": "[auth] compiling", "category": "system", "ts": 1.0},
            {"text": "[api] testing", "category": "system", "ts": 2.0},
            {"text": "[auth] done", "category": "system", "ts": 3.0},
        ]
        w = LogViewerWidget()
        state = _make_state(tool_events=events)
        w.refresh_from_state(state)
        w.toggle_filter("auth")
        w.refresh_from_state(state)
        # After filtering, only auth events should appear

    def test_toggle_filter_clears(self):
        w = LogViewerWidget()
        w.toggle_filter("auth")
        assert w.filter_task == "auth"
        w.toggle_filter("auth")
        assert w.filter_task is None

    def test_toggle_filter_switches(self):
        w = LogViewerWidget()
        w.toggle_filter("auth")
        assert w.filter_task == "auth"
        w.toggle_filter("api")
        assert w.filter_task == "api"


class TestStatusBarWidget:
    def test_refresh_basic(self):
        state = _make_state(phase="implement", phase_pct=60)
        w = StatusBarWidget()
        w.refresh_from_state(state)

    def test_refresh_with_tasks_and_tokens(self):
        state = _make_state(
            phase="test",
            subtasks={
                "a": SubtaskInfo(name="a", agent="c", pane_id=1, status="done"),
                "b": SubtaskInfo(name="b", agent="c", pane_id=2, status="error"),
            },
            total_input_tokens=5000,
            total_output_tokens=8000,
        )
        w = StatusBarWidget()
        w.refresh_from_state(state)


# -- Textual app integration tests --


@pytest.fixture()
def bridge_with_data() -> DashboardBridge:
    b = DashboardBridge("integration test")
    b.update_phase("implement", 50)
    b.update_subtask("auth", "claude", 1, "running", started_at=1.0)
    b.update_subtask("api", "codex", 2, "done", started_at=1.0, finished_at=2.0)
    b.add_tool_event("hello world")
    return b


@pytest.fixture()
def bridge_with_error() -> DashboardBridge:
    b = DashboardBridge("error test")
    b.update_subtask("auth", "claude", 1, "error", started_at=1.0, finished_at=2.0)
    b.update_subtask("api", "codex", 2, "running", started_at=1.0)
    return b


async def test_app_composes(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)):
        assert app.query_one(TaskListWidget) is not None
        assert app.query_one(LogViewerWidget) is not None
        assert app.query_one(StatusBarWidget) is not None


async def test_app_refresh_populates_widgets(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()


async def test_dag_screen_toggle(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DagScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DagScreen)


async def test_quit_binding(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("q")


async def test_cursor_navigation(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        tl = app.query_one(TaskListWidget)
        assert tl.selected_task == "auth"
        await pilot.press("j")
        await pilot.pause()
        assert tl.selected_task == "api"
        await pilot.press("k")
        await pilot.pause()
        assert tl.selected_task == "auth"


async def test_cursor_arrow_keys(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        tl = app.query_one(TaskListWidget)
        await pilot.press("down")
        await pilot.pause()
        assert tl.selected_task == "api"
        await pilot.press("up")
        await pilot.pause()
        assert tl.selected_task == "auth"


async def test_cancel_running_task(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert app.pending_cancel is not None
        assert app.pending_cancel.task_name == "auth"


async def test_cancel_completed_task_warns(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        tl = app.query_one(TaskListWidget)
        tl.move_cursor(1)  # select "api" (done)
        await pilot.press("c")
        await pilot.pause()
        assert app.pending_cancel is None


async def test_retry_error_task(bridge_with_error):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_error)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert app.pending_retry is not None
        assert app.pending_retry.task_name == "auth"


async def test_retry_non_error_warns(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert app.pending_retry is None


async def test_filter_logs(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_dashboard()
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        lv = app.query_one(LogViewerWidget)
        assert lv.filter_task == "auth"


async def test_help_screen(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_help_screen_h_key(bridge_with_data):
    from openmax.tui.app import OpenMaxApp

    app = OpenMaxApp(bridge_with_data)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
