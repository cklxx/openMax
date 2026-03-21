"""DAG visualization screen — live-updating modal overlay showing task dependency graph."""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from openmax.tui.bridge import DashboardBridge
from openmax.tui.dag import render_dag


class DagScreen(Screen):
    """Modal screen showing live-updating DAG visualization."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", show=True),
        Binding("d", "dismiss_screen", "Close"),
    ]

    DEFAULT_CSS = """
    DagScreen {
        align: center middle;
    }
    #dag-content {
        width: 80%;
        height: 80%;
        padding: 2;
        border: round $primary;
        overflow-y: auto;
    }
    """

    def __init__(self, bridge: DashboardBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._last_version = -1

    def compose(self):
        yield Static("", id="dag-content")

    def on_mount(self) -> None:
        self._refresh_dag()
        self.set_interval(1.5, self._refresh_dag)

    def _refresh_dag(self) -> None:
        if self._bridge.version == self._last_version:
            return
        self._last_version = self._bridge.version
        state = self._bridge.get_snapshot()
        statuses = {n: info.status for n, info in state.subtasks.items()}
        task_names = list(state.subtasks.keys())
        width = self.app.size.width if self.app else 120
        content = render_dag(
            [task_names],
            statuses,
            deps=state.task_dependencies or None,
            terminal_width=int(width * 0.8),
        )
        self.query_one("#dag-content", Static).update(content if content else "(no tasks)")

    def action_dismiss_screen(self) -> None:
        self.dismiss()
