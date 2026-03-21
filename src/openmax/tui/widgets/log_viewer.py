"""Log viewer widget showing scrolling tool events."""

from __future__ import annotations

from textual.widgets import Static

from openmax.tui.bridge import DashboardState


class LogViewerWidget(Static):
    """Scrolling log output from tool events."""

    DEFAULT_CSS = """
    LogViewerWidget {
        width: 100%;
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    """

    def refresh_from_state(self, state: DashboardState) -> None:
        lines = [evt.get("text", "") for evt in state.tool_events[-200:]]
        self.update("\n".join(lines) if lines else "(no events)")
