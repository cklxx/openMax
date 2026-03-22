"""OpenMax Textual TUI application."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal

from openmax.tui.bridge import DashboardBridge
from openmax.tui.widgets import (
    DagScreen,
    HelpScreen,
    LogViewerWidget,
    StatusBarWidget,
    TaskListWidget,
)


class CancelTaskRequest:
    """Event payload for task cancellation requests."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name


class RetryTaskRequest:
    """Event payload for task retry requests."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name


class OpenMaxApp(App):
    """Main Textual application for the openMax TUI dashboard."""

    CSS = """
    #task-panel {
        width: 30%;
        height: 100%;
        border-right: solid $primary;
    }
    #log-panel {
        width: 70%;
        height: 100%;
    }
    #main-area {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "toggle_dag", "DAG View"),
        Binding("tab", "focus_next", "Next Panel"),
        Binding("j", "cursor_down", "Next Task", show=False),
        Binding("k", "cursor_up", "Prev Task", show=False),
        Binding("down", "cursor_down", "Next Task", show=False),
        Binding("up", "cursor_up", "Prev Task", show=False),
        Binding("c", "cancel_task", "Cancel Task"),
        Binding("r", "retry_task", "Retry Task"),
        Binding("l", "filter_logs", "Filter Logs"),
        Binding("question_mark", "show_help", "Help"),
        Binding("h", "show_help", "Help", show=False),
    ]

    def __init__(self, bridge: DashboardBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._last_version = -1
        self.pending_cancel: CancelTaskRequest | None = None
        self.pending_retry: RetryTaskRequest | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(
            TaskListWidget(id="task-panel"),
            LogViewerWidget(id="log-panel"),
            id="main-area",
        )
        yield StatusBarWidget(id="status-bar")

    def on_mount(self) -> None:
        self.set_interval(0.5, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        if self._bridge.version == self._last_version:
            return
        self._last_version = self._bridge.version
        state = self._bridge.get_snapshot()
        self.query_one(TaskListWidget).refresh_from_state(state)
        self.query_one(LogViewerWidget).refresh_from_state(state)
        self.query_one(StatusBarWidget).refresh_from_state(state)

    def action_toggle_dag(self) -> None:
        self.push_screen(DagScreen(self._bridge))

    def action_cursor_down(self) -> None:
        self.query_one(TaskListWidget).move_cursor(1)

    def action_cursor_up(self) -> None:
        self.query_one(TaskListWidget).move_cursor(-1)

    def _selected_task_or_notify(self) -> str | None:
        task = self.query_one(TaskListWidget).selected_task
        if task is None:
            self.notify("Select a task first", severity="warning")
        return task

    def action_cancel_task(self) -> None:
        task = self._selected_task_or_notify()
        if task is None:
            return
        status = self.query_one(TaskListWidget).task_status(task)
        if status in ("done", "error"):
            self.notify("Task already completed", severity="warning")
            return
        self.pending_cancel = CancelTaskRequest(task)
        self.notify(f"Cancel requested: {task}")

    def action_retry_task(self) -> None:
        task = self._selected_task_or_notify()
        if task is None:
            return
        status = self.query_one(TaskListWidget).task_status(task)
        if status != "error":
            self.notify("Only failed tasks can be retried", severity="warning")
            return
        self.pending_retry = RetryTaskRequest(task)
        self.notify(f"Retry requested: {task}")

    def action_filter_logs(self) -> None:
        task = self._selected_task_or_notify()
        if task is None:
            return
        self.query_one(LogViewerWidget).toggle_filter(task)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
