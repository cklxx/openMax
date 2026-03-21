"""Log viewer widget showing scrolling tool events with optional task filter."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from openmax.tui.bridge import DashboardState


class LogViewerWidget(Static):
    """Scrolling log output from tool events, with optional task name filter."""

    DEFAULT_CSS = """
    LogViewerWidget {
        width: 100%;
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    """

    filter_task: reactive[str | None] = reactive(None)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._last_state: DashboardState | None = None

    def watch_filter_task(self) -> None:
        if self._last_state is not None:
            self._render_logs(self._last_state)

    def toggle_filter(self, task_name: str | None) -> None:
        if self.filter_task == task_name:
            self.filter_task = None
        else:
            self.filter_task = task_name

    def refresh_from_state(self, state: DashboardState) -> None:
        self._last_state = state
        self._render_logs(state)

    def _render_logs(self, state: DashboardState) -> None:
        events = state.tool_events[-200:]
        if self.filter_task:
            task = self.filter_task
            events = [e for e in events if task in e.get("text", "")]
        lines = [evt.get("text", "") for evt in events]
        header = f"[filter: {self.filter_task}]\n" if self.filter_task else ""
        body = "\n".join(lines) if lines else "(no events)"
        self.update(f"{header}{body}")
