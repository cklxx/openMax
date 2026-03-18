"""Compact status bar and shared console for openMax runs.

Design: vite-inspired streaming log + lazy bottom status bar.
Status bar appears only when subtasks exist. Progress is derived from
subtask states. A connecting spinner shows during SDK warmup so the
user knows the tool is alive.
"""

from __future__ import annotations

import sys
import time

from rich.console import ConsoleRenderable, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from openmax.output import console

_MAX_TOOL_EVENTS = 8

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
    console.print(Rule(f" {phase} ", style="dim cyan", align="left"))
    console.print()


def print_agent_text(text: str) -> None:
    """Render lead-agent text as markdown."""
    if text.strip():
        console.print(Markdown(text))


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
        """Mark dashboard as ready and show startup spinner."""
        self._active = True
        if self._is_tty:
            self._spinner_live = Live(
                self._spinner_renderable("starting up"),
                console=console,
                refresh_per_second=4,
                transient=True,
            )
            self._spinner_live.start()

    @staticmethod
    def _spinner_renderable(label: str):
        from rich.columns import Columns
        from rich.spinner import Spinner

        return Columns(
            [Spinner("dots", style="dim cyan"), Text(label, style="dim")], padding=(0, 1)
        )

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
        """Called when the first SDK response arrives — switch to 'thinking' state."""
        if self._spinner_live is not None:
            self._spinner_live.update(self._spinner_renderable("thinking"))

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
        # Convert wall-clock timestamps (time.time) to monotonic for display.
        # The dashboard timer uses time.monotonic() throughout.
        mono_started = existing.get("started_at")
        mono_finished = existing.get("finished_at")
        now_mono = time.monotonic()
        if started_at is not None and mono_started is None:
            # First time setting started_at — record current monotonic time
            mono_started = now_mono
        if finished_at is not None and mono_finished is None:
            mono_finished = now_mono
        self.subtasks[name] = {
            "agent": agent,
            "pane_id": pane_id,
            "status": status,
            "started_at": mono_started,
            "finished_at": mono_finished,
        }
        self._ensure_live()
        self._refresh()

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        self.pane_activity[pane_id] = last_line
        self._refresh()

    def add_tool_event(self, text: str, category: str = "system") -> None:
        self._stop_spinner()
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
        """Rich status bar with subtask table inside a panel + progress bar."""
        # Subtask table — compact, no header (badges are self-explanatory)
        tbl = Table(
            show_header=False,
            show_edge=False,
            pad_edge=False,
            padding=(0, 1),
            expand=False,
        )
        tbl.add_column(width=2, no_wrap=True)
        tbl.add_column(style="bold", no_wrap=True, max_width=28)
        tbl.add_column(style="dim", no_wrap=True, max_width=14)
        tbl.add_column(style="dim", justify="right", no_wrap=True, width=5)
        tbl.add_column(style="dim", justify="right", no_wrap=True, width=6)

        for name, info in self.subtasks.items():
            status = info.get("status", "pending")
            badge_char, badge_style = _STATUS_BADGES.get(status, ("\u25cb", "dim"))
            badge = Text(badge_char, style=badge_style)
            agent = info.get("agent", "")
            pane_str = f"#{info['pane_id']}" if info.get("pane_id") is not None else ""
            elapsed = _elapsed_since(info.get("started_at"), info.get("finished_at"))
            tbl.add_row(badge, name, agent, pane_str, elapsed)

        # Progress counts
        elapsed = _elapsed(self.start_time)
        total = len(self.subtasks)
        counts: dict[str, int] = {}
        for info in self.subtasks.values():
            st = info.get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        done = counts.get("done", 0)

        # Build progress bar with eighth-blocks for smooth rendering
        bar_width = 20
        ratio = done / total if total else 0
        filled_full = int(ratio * bar_width)
        remainder = (ratio * bar_width) - filled_full
        partial_chars = " \u258f\u258e\u258d\u258c\u258b\u258a\u2589\u2588"
        partial_idx = int(remainder * 8)

        bar_color = "green" if done == total else "cyan"
        progress = Text("  ")
        progress.append("\u2588" * filled_full, style=bar_color)
        if filled_full < bar_width:
            progress.append(partial_chars[partial_idx], style=bar_color)
            progress.append("\u2591" * (bar_width - filled_full - 1), style="dim")
        progress.append(f" {done}/{total}", style="bold")
        progress.append(f"  {elapsed}", style="dim")

        status_parts = []
        if counts.get("running"):
            status_parts.append(f"{counts['running']} running")
        if counts.get("error"):
            status_parts.append(f"{counts['error']} err")
        if counts.get("pending"):
            status_parts.append(f"{counts['pending']} queued")
        if status_parts:
            joined = " \u2022 ".join(status_parts)
            progress.append(f"  {joined}", style="dim")

        if self._monitor_count > 0:
            progress.append(f"  [{self._monitor_count} checks]", style="dim")

        # Wrap table in a panel with dim border
        panel = Panel(
            Group(tbl, progress),
            title="[bold]agents[/bold]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )

        return panel

    def _refresh(self) -> None:
        if self._live is not None and self._active:
            self._live.update(self._render())
