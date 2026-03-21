"""OpenMax Textual TUI application."""

from __future__ import annotations

import signal
import threading

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
        self._last_version = -1

    def _build_driver(
        self,
        headless: bool = False,
        inline: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ):
        if threading.current_thread() is not threading.main_thread():
            original = signal.signal
            signal.signal = lambda *_a, **_kw: signal.SIG_DFL
            try:
                return super()._build_driver(headless, inline, mouse, size)
            finally:
                signal.signal = original
        return super()._build_driver(headless, inline, mouse, size)

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
        state = self._bridge.get_snapshot()
        self.push_screen(DagScreen(state))
