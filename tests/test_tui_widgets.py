"""Tests for openmax.tui.widgets — unit + Textual app integration."""

from __future__ import annotations

import time

import pytest

from openmax.tui.bridge import DashboardBridge, DashboardState, SubtaskInfo
from openmax.tui.widgets import (
    DagScreen,
    LogViewerWidget,
    StatusBarWidget,
    TaskListWidget,
    _elapsed_str,
    _task_icon,
)

# -- Unit tests for helpers --


def test_task_icon_known_statuses():
    assert _task_icon("done") == "\u2713"
    assert _task_icon("running") == "\u25cf"
    assert _task_icon("pending") == "\u25cb"
    assert _task_icon("error") == "\u2717"


def test_task_icon_unknown_status():
    assert _task_icon("unknown") == "?"


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


# -- Widget refresh_from_state tests (unit, no Textual app) --


def _make_state(**overrides) -> DashboardState:
    defaults = {"goal": "test"}
    defaults.update(overrides)
    return DashboardState(**defaults)


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
            }
        )
        w = TaskListWidget()
        w.refresh_from_state(state)


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
