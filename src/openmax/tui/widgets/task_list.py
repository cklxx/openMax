"""Task list widget showing subtask status indicators with selection support."""

from __future__ import annotations

import time

from textual.message import Message
from textual.reactive import reactive
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
    """Displays task list with status indicators and cursor selection."""

    DEFAULT_CSS = """
    TaskListWidget {
        width: 100%;
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    """

    selected_index: reactive[int] = reactive(0)

    class Selected(Message):
        """Posted when the selected task changes."""

        def __init__(self, task_name: str | None) -> None:
            super().__init__()
            self.task_name = task_name

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._task_names: list[str] = []

    @property
    def selected_task(self) -> str | None:
        if not self._task_names:
            return None
        idx = max(0, min(self.selected_index, len(self._task_names) - 1))
        return self._task_names[idx]

    def task_status(self, task_name: str) -> str | None:
        return self._task_statuses.get(task_name)

    def move_cursor(self, delta: int) -> None:
        if not self._task_names:
            return
        new = max(0, min(self.selected_index + delta, len(self._task_names) - 1))
        if new != self.selected_index:
            self.selected_index = new

    def watch_selected_index(self) -> None:
        self.post_message(self.Selected(self.selected_task))
        self._render_tasks()

    def refresh_from_state(self, state: DashboardState) -> None:
        self._task_names = list(state.subtasks.keys())
        self._task_statuses = {n: info.status for n, info in state.subtasks.items()}
        self._state = state
        if self.selected_index >= len(self._task_names) and self._task_names:
            self.selected_index = len(self._task_names) - 1
        self._render_tasks()

    def _render_tasks(self) -> None:
        if not hasattr(self, "_state"):
            return
        lines: list[str] = []
        for i, (name, info) in enumerate(self._state.subtasks.items()):
            icon = _task_icon(info.status)
            pct = self._state.task_progress.get(info.name)
            prog = _progress_bar(pct, info.status)
            elapsed = _elapsed_str(info.started_at, info.finished_at)
            lines.append(f"{icon} {info.name:<20s} {prog:<14s} {info.agent:<10s} {elapsed}")
            marker = ">" if i == self.selected_index else " "
            lines.append(f"{marker} {icon} {info.name:<20s} {info.agent:<10s} {elapsed}")
        self.update("\n".join(lines) if lines else "(no tasks)")
