"""Compact status bar and shared console for openMax runs.

Design: vite-inspired streaming log + lazy bottom status bar.
Status bar appears only when subtasks exist. Progress is derived from
subtask states. A connecting spinner shows during SDK warmup so the
user knows the tool is alive.
"""

from __future__ import annotations

import sys
import time

from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# Shared console instance — import this in other modules to avoid Live/print overlap.
console = Console()

_MAX_TOOL_EVENTS = 8

# Consistent prefix for log lines (vite-style).
P = "\u279c"  # ➜

# Status indicators for subtask states.
_STATUS_BADGES: dict[str, tuple[str, str]] = {
    "running": ("\u25cf", "yellow"),  # ●
    "done": ("\u2713", "green"),  # ✓
    "error": ("\u2717", "red"),  # ✗
    "pending": ("\u25cb", "dim"),  # ○
}


def _elapsed(start: float) -> str:
    secs = int(time.monotonic() - start)
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


def _elapsed_since(start: float | None, end: float | None = None) -> str:
    """Elapsed time from a monotonic timestamp to now (or to end)."""
    if start is None:
        return ""
    ref = end if end is not None else time.monotonic()
    secs = max(0, int(ref - start))
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


def print_phase_divider(phase: str) -> None:
    """Print a styled phase divider to the console."""
    console.print()
    console.print(Rule(f" {phase} ", style="cyan", align="left"))
    console.print()


def print_agent_text(text: str) -> None:
    """Print lead-agent reasoning text with visual distinction."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            console.print(f"  [dim]\u2502[/dim] {stripped}")


class RunDashboard:
    """Compact bottom status bar rendered via Rich Live.

    The Live widget is lazy — it only starts when there is at least one subtask.
    Before that, a lightweight "connecting" spinner is shown during SDK warmup.
    """

    def __init__(self, goal: str) -> None:
        self.goal = goal[:60]
        self.start_time = time.monotonic()
        self.phase = "starting"
        self.subtasks: dict[str, dict] = {}
        self.pane_activity: dict[int, str] = {}
        self.tool_events: list[dict] = []
        self._live: Live | None = None
        self._spinner_live: Live | None = None
        self._active = False
        self._is_tty = sys.stdout.isatty()
        self._last_phase: str | None = None
        self._monitor_count = 0

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Mark dashboard as ready and show connecting spinner."""
        self._active = True
        if self._is_tty:
            self._spinner_live = Live(
                Text("  [dim]connecting...[/dim]"),
                console=console,
                refresh_per_second=2,
                transient=True,
            )
            self._spinner_live.start()

    def _stop_spinner(self) -> None:
        if self._spinner_live is not None:
            self._spinner_live.update(Text(""))
            self._spinner_live.stop()
            self._spinner_live = None

    def _ensure_live(self) -> None:
        """Create the subtask status bar on first subtask."""
        if self._live is not None or not self._is_tty or not self._active:
            return
        self._stop_spinner()
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=2,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        self._stop_spinner()
        if self._live is not None:
            self._live.update(Text(""))
            self._live.stop()
            self._live = None
        self._active = False

    def mark_connected(self) -> None:
        """Called when the first SDK response arrives — dismiss the spinner."""
        self._stop_spinner()

    # ── State updates ─────────────────────────────────────────────

    def update_phase(self, phase: str, pct: int | None = None) -> None:
        old_phase = self.phase
        self.phase = phase
        if phase != old_phase and self._last_phase != phase:
            self._last_phase = phase
            # Temporarily stop live to print the divider cleanly
            if self._live is not None:
                self._live.update(Text(""))
                self._live.stop()
                self._live = None
            print_phase_divider(phase)
            # Restart live if we have subtasks
            if self.subtasks:
                self._ensure_live()
        elif self.subtasks:
            self._ensure_live()
        self._refresh()

    def update_subtask(
        self,
        name: str,
        agent: str,
        pane_id: int | None,
        status: str,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> None:
        existing = self.subtasks.get(name, {})
        self.subtasks[name] = {
            "agent": agent,
            "pane_id": pane_id,
            "status": status,
            "started_at": started_at or existing.get("started_at"),
            "finished_at": finished_at or existing.get("finished_at"),
        }
        self._ensure_live()
        self._refresh()

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        self.pane_activity[pane_id] = last_line
        self._refresh()

    def add_tool_event(self, text: str, category: str = "system") -> None:
        self.tool_events.append({"text": text, "category": category, "ts": time.monotonic()})
        if len(self.tool_events) > _MAX_TOOL_EVENTS:
            self.tool_events = self.tool_events[-_MAX_TOOL_EVENTS:]
        if category == "monitor":
            self._monitor_count += 1
        self._refresh()

    def bump_monitor_count(self) -> None:
        """Increment the monitoring check counter (for collapsed display)."""
        self._monitor_count += 1
        self._refresh()

    # ── Rendering ─────────────────────────────────────────────────

    def _render(self) -> ConsoleRenderable:
        total = len(self.subtasks)
        if total == 0:
            return self._render_simple()
        return self._render_full()

    def _render_simple(self) -> Text:
        """Minimal status line when no subtasks exist yet."""
        elapsed = _elapsed(self.start_time)
        line = Text()
        line.append(f"  {self.phase}", style="bold cyan")
        line.append(f"  {elapsed}", style="dim")
        return line

    def _render_full(self) -> ConsoleRenderable:
        """Rich status bar with subtask table + progress bar."""
        parts: list[ConsoleRenderable] = []

        # Subtask table
        tbl = Table(
            show_header=False,
            show_edge=False,
            pad_edge=False,
            padding=(0, 1),
            expand=False,
        )
        tbl.add_column("indicator", width=2, no_wrap=True)
        tbl.add_column("name", style="bold", no_wrap=True, max_width=28)
        tbl.add_column("agent", style="dim", no_wrap=True, max_width=14)
        tbl.add_column("pane", style="dim", justify="right", no_wrap=True, width=7)
        tbl.add_column("elapsed", style="dim", justify="right", no_wrap=True, width=6)

        for name, info in self.subtasks.items():
            status = info.get("status", "pending")
            badge_char, badge_style = _STATUS_BADGES.get(status, ("\u25cb", "dim"))
            badge = Text(badge_char, style=badge_style)
            agent = info.get("agent", "")
            pane_str = f"#{info['pane_id']}" if info.get("pane_id") is not None else ""
            elapsed = _elapsed_since(info.get("started_at"), info.get("finished_at"))
            tbl.add_row(badge, name, agent, pane_str, elapsed)

        parts.append(tbl)

        # Progress bar line
        elapsed = _elapsed(self.start_time)
        total = len(self.subtasks)
        counts: dict[str, int] = {}
        for info in self.subtasks.values():
            st = info.get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        done = counts.get("done", 0)

        bar_width = 24
        filled = int(done / total * bar_width) if total else 0
        bar_filled = "\u2501" * filled  # ━
        bar_empty = "\u2504" * (bar_width - filled)  # ┄
        bar_color = "green" if done == total else "cyan"

        progress = Text()
        progress.append(f"  {bar_filled}", style=bar_color)
        progress.append(bar_empty, style="dim")
        progress.append(f"  {done}/{total}", style="bold")
        progress.append(f"  {elapsed}", style="dim")

        status_parts = []
        if counts.get("running"):
            status_parts.append(f"{counts['running']} running")
        if counts.get("error"):
            status_parts.append(f"{counts['error']} err")
        if counts.get("pending"):
            status_parts.append(f"{counts['pending']} queued")
        if status_parts:
            progress.append(f"  ({', '.join(status_parts)})", style="dim")

        if self._monitor_count > 0:
            progress.append(f"  [{self._monitor_count} checks]", style="dim")

        parts.append(progress)

        return Group(*parts)

    def _refresh(self) -> None:
        if self._live is not None and self._active:
            self._live.update(self._render())
