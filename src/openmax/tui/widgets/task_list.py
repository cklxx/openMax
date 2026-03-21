"""Task list widget showing subtask status indicators."""

from __future__ import annotations

import time

from textual.widgets import Static

from openmax.tui.bridge import DashboardState

_STATUS_ICONS = {"done": "\u2713", "running": "\u25cf", "pending": "\u25cb", "error": "\u2717"}


def _task_icon(status: str) -> str:
    return _STATUS_ICONS.get(status, "?")


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
