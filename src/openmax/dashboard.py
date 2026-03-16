"""Rich Live Dashboard for real-time openMax run progress."""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_STATUS_ICONS = {
    "done": "✅",
    "running": "🔄",
    "error": "❌",
    "pending": "⏳",
}

# Shared console instance — import this in other modules to avoid Live/print overlap.
console = Console()


def _elapsed(start: float) -> str:
    secs = int(time.monotonic() - start)
    m, s = divmod(secs, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


_MAX_TOOL_EVENTS = 8


class RunDashboard:
    def __init__(self, goal: str) -> None:
        self.goal = goal[:60]
        self.start_time = time.monotonic()
        self.phase = "starting"
        self.completion_pct: int | None = None
        self.subtasks: dict[str, dict] = {}  # name -> {agent, pane_id, status}
        self.pane_activity: dict[int, str] = {}  # pane_id -> last_line
        self.tool_events: list[dict] = []  # {text, category, ts}
        self._live: Live | None = None
        self._active = False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        if not sys.stdout.isatty():
            return
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=4,
            transient=False,
        )
        self._live.start()
        self._active = True

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._active = False

    # ── State updates ─────────────────────────────────────────────

    def update_phase(self, phase: str, pct: int | None = None) -> None:
        self.phase = phase
        if pct is not None:
            self.completion_pct = pct
        self._refresh()

    def update_subtask(
        self,
        name: str,
        agent: str,
        pane_id: int | None,
        status: str,
    ) -> None:
        self.subtasks[name] = {"agent": agent, "pane_id": pane_id, "status": status}
        self._refresh()

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        self.pane_activity[pane_id] = last_line
        self._refresh()

    def add_tool_event(self, text: str, category: str = "system") -> None:
        self.tool_events.append(
            {
                "text": text,
                "category": category,
                "ts": time.monotonic(),
            }
        )
        if len(self.tool_events) > _MAX_TOOL_EVENTS:
            self.tool_events = self.tool_events[-_MAX_TOOL_EVENTS:]
        self._refresh()

    # ── Rendering ─────────────────────────────────────────────────

    def _render(self) -> Panel:
        lines: list[Text | Table | str] = []

        # Phase + progress bar
        phase_row = Table.grid(expand=True)
        phase_row.add_column(ratio=1)
        phase_row.add_column(justify="right", width=14)

        pct = self.completion_pct or 0
        bar_filled = int(pct / 10)
        bar_empty = 10 - bar_filled
        bar_str = "█" * bar_filled + "░" * bar_empty

        phase_row.add_row(
            Text(f"Phase: {self.phase.title()}", style="bold cyan"),
            Text(f"{bar_str}  {pct}%", style="green"),
        )
        lines.append(phase_row)

        # Subtasks table
        if self.subtasks:
            lines.append(Text(""))
            tbl = Table(
                show_header=True,
                header_style="bold dim",
                box=None,
                padding=(0, 1),
            )
            tbl.add_column("", width=2)  # icon
            tbl.add_column("Sub-task", style="bold")
            tbl.add_column("Agent", style="dim")
            tbl.add_column("Pane", justify="right")
            tbl.add_column("Status")

            for name, info in self.subtasks.items():
                st = info.get("status", "pending")
                icon = _STATUS_ICONS.get(st, "⏳")
                pane_id = info.get("pane_id")
                pane_str = str(pane_id) if pane_id is not None else "-"
                color = {
                    "done": "green",
                    "running": "yellow",
                    "error": "red",
                    "pending": "dim",
                }.get(st, "white")
                tbl.add_row(
                    icon,
                    name,
                    info.get("agent", ""),
                    pane_str,
                    Text(st, style=color),
                )
            lines.append(tbl)

        # Tool activity
        if self.tool_events:
            lines.append(Text(""))
            lines.append(Text("Tool Activity", style="bold dim"))
            _cat_colors = {
                "dispatch": "green",
                "monitor": "cyan",
                "intervention": "yellow",
                "system": "dim",
            }
            for evt in self.tool_events[-5:]:
                color = _cat_colors.get(evt["category"], "dim")
                age = int(time.monotonic() - evt["ts"])
                age_str = f"{age}s ago" if age > 0 else "now"
                lines.append(Text(f"  [{age_str}] {evt['text'][:70]}", style=color))

        # Latest pane activity (up to 3 panes)
        if self.pane_activity:
            lines.append(Text(""))
            recent_panes = list(self.pane_activity.items())[-3:]
            for pane_id, last_line in recent_panes:
                preview = last_line[:70].strip()
                lines.append(Text(f"Pane {pane_id}: {preview!r}", style="dim"))

        # Build the inner grid
        grid = Table.grid(expand=True)
        grid.add_column()
        for item in lines:
            grid.add_row(item)

        elapsed = _elapsed(self.start_time)
        title = Text()
        title.append("openMax", style="bold blue")
        title.append(" · ")
        title.append(self.goal, style="italic")
        title.append(f"  [{elapsed}]", style="dim")

        return Panel(grid, title=title, border_style="blue", expand=True)

    def _refresh(self) -> None:
        if self._live is not None and self._active:
            self._live.update(self._render())
