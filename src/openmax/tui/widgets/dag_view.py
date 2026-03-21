"""DAG visualization screen — modal overlay showing task dependency graph."""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from openmax.tui.bridge import DashboardState
from openmax.tui.dag import render_dag


class DagScreen(Screen):
    """Modal screen showing DAG visualization."""

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

    def __init__(self, state: DashboardState) -> None:
        super().__init__()
        self._state = state

    def compose(self):
        statuses = {name: info.status for name, info in self._state.subtasks.items()}
        task_names = list(self._state.subtasks.keys())
        content = render_dag([task_names], statuses) if task_names else "(no tasks)"
        yield Static(content, id="dag-content")

    def action_dismiss_screen(self) -> None:
        self.dismiss()
