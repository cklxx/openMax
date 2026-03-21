"""OpenMax Textual TUI application."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal

from openmax.tui.bridge import DashboardBridge
from openmax.tui.widgets import (
    DagScreen,
    LogViewerWidget,
    StatusBarWidget,
    TaskListWidget,
)


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
    ]

    def __init__(self, bridge: DashboardBridge) -> None:
        super().__init__()
        self._bridge = bridge

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
        state = self._bridge.get_snapshot()
        self.query_one(TaskListWidget).refresh_from_state(state)
        self.query_one(LogViewerWidget).refresh_from_state(state)
        self.query_one(StatusBarWidget).refresh_from_state(state)

    def action_toggle_dag(self) -> None:
        state = self._bridge.get_snapshot()
        self.push_screen(DagScreen(state))
