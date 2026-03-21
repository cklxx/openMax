"""Task list widget showing subtask status indicators."""

from __future__ import annotations

import time

from textual.widgets import Static

from openmax.tui.bridge import DashboardState
from openmax.tui.dag import STATUS_SYMBOLS


def _task_icon(status: str) -> str:
    return STATUS_SYMBOLS.get(status, "?")


def _progress_bar(pct: int | None, status: str) -> str:
    """Compact progress bar: [████░░] 52% — max ~14 chars."""
    if status == "done":
        return "\u2714"
    if status == "error":
        return "\u2718"
    if pct is None:
        return "[\u00b7\u00b7\u00b7]" if status == "running" else ""
    pct = max(0, min(100, pct))
    bar_w = 6
    filled = int(pct / 100 * bar_w)
    bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
    return f"[{bar}] {pct}%"


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
            pct = state.task_progress.get(info.name)
            prog = _progress_bar(pct, info.status)
            elapsed = _elapsed_str(info.started_at, info.finished_at)
            lines.append(f"{icon} {info.name:<20s} {prog:<14s} {info.agent:<10s} {elapsed}")
        self.update("\n".join(lines) if lines else "(no tasks)")
