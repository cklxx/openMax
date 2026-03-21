"""Task list widget showing subtask status indicators."""

from __future__ import annotations

import time

from textual.widgets import Static

from openmax.formatting import _ACCESSIBLE_LABELS, is_accessible_mode
from openmax.tui.bridge import DashboardState
from openmax.tui.dag import STATUS_SYMBOLS


def _task_icon(status: str) -> str:
    icon = STATUS_SYMBOLS.get(status, "?")
    if is_accessible_mode():
        label = _ACCESSIBLE_LABELS.get(status, "")
        return f"{icon} {label}" if label else icon
    return icon


def _elapsed_str(started: float | None, finished: float | None) -> str:
    if started is None:
        return "--"
    end = finished if finished is not None else time.monotonic()
    return f"{end - started:.0f}s"


class TaskListWidget(Static):
    """Displays task list with status indicators."""

    DEFAULT_CSS = """
    TaskListWidget {
        width: 100%;
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    """

    def refresh_from_state(self, state: DashboardState) -> None:
        lines: list[str] = []
        for info in state.subtasks.values():
            icon = _task_icon(info.status)
            elapsed = _elapsed_str(info.started_at, info.finished_at)
            lines.append(f"{icon} {info.name:<20s} {info.agent:<10s} {elapsed}")
        self.update("\n".join(lines) if lines else "(no tasks)")
