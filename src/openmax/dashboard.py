"""Compact status bar and shared console for openMax runs.

Design: vite-inspired streaming log + lazy bottom status bar.
Status bar appears only when subtasks exist. Progress is derived from
subtask states. A connecting spinner shows during SDK warmup so the
user knows the tool is alive.
"""

from __future__ import annotations

import io
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from openmax.output import console

_MAX_TOOL_EVENTS = 8
_MAX_TASK_NAME = 24

# Status indicators for subtask states.
_STATUS_BADGES: dict[str, tuple[str, str]] = {
    "running": ("\u25c9", "yellow"),  # ◉
    "done": ("\u2714", "green"),  # ✔
    "error": ("\u2718", "red"),  # ✘
    "pending": ("\u25cb", "dim"),  # ○
}

# Sort priority: running first, pending, done, error last.
_STATUS_SORT_ORDER: dict[str, int] = {
    "running": 0,
    "pending": 1,
    "done": 2,
    "error": 3,
}

_ROW_STYLES: dict[str, str] = {
    "running": "bold",
    "done": "dim strike",
    "error": "bold red",
    "pending": "dim",
}


@runtime_checkable
class DashboardProtocol(Protocol):
    """Interface that all dashboard implementations must satisfy."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def mark_connected(self) -> None: ...
    def update_phase(self, phase: str, pct: int | None = None) -> None: ...

    def update_subtask(
        self,
        name: str,
        agent: str,
        pane_id: int | None,
        status: str,
        started_at: float | None = None,
        finished_at: float | None = None,
        estimated_minutes: int | None = None,
    ) -> None: ...

    def update_pane_activity(self, pane_id: int, last_line: str) -> None: ...
    def add_tool_event(self, text: str, category: str = "system") -> None: ...

    def set_session_metrics(
        self,
        *,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        acceleration_ratio: float | None = None,
        critical_path_seconds: float | None = None,
    ) -> None: ...

    def set_dispatch_prompt(self, name: str, prompt: str) -> None: ...
    def bump_monitor_count(self) -> None: ...


@dataclass
class SessionMetrics:
    """Aggregated session metrics for the done banner."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    acceleration_ratio: float | None = None
    critical_path_seconds: float | None = None
    estimated_human_minutes: dict[str, int] = field(default_factory=dict)


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


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    return f"{m}m {s:02d}s"


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if it exceeds max_len."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _format_tokens(count: int) -> str:
    """Format token count with k/M suffix."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def print_phase_divider(phase: str) -> None:
    """Print a styled phase divider to the console."""
    console.print()
    console.print(Rule(f" {phase} ", style="dim cyan", align="left"))
    console.print()


def print_agent_text(text: str) -> None:
    """Render lead-agent text as markdown; collapse 3+ consecutive newlines to 2."""
    stripped = text.strip()
    if not stripped:
        return
    buf = io.StringIO()
    tmp = Console(file=buf, width=console.width or 80, force_terminal=True, no_color=False)
    tmp.print(Markdown(stripped), end="")
    rendered = re.sub(r"\n{3,}", "\n\n", buf.getvalue())
    console.print(Text.from_ansi(rendered.strip()))


def render_session_summary(
    subtasks: dict[str, dict],
    metrics: SessionMetrics,
    wall_seconds: float,
) -> Panel:
    """Render a standalone session summary panel. Pure function."""
    parts: list[ConsoleRenderable] = []
    parts.append(_build_metrics_table(subtasks, metrics, wall_seconds))
    parts.append(Text())
    parts.append(_build_breakdown_table(subtasks, metrics))
    return Panel(
        Group(*parts),
        title="[bold]Session Summary[/bold]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
    )


def _build_metrics_table(
    subtasks: dict[str, dict],
    metrics: SessionMetrics,
    wall_seconds: float,
) -> Table:
    """Build the top-level metrics key-value table."""
    tbl = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    tbl.add_column(style="bold cyan", no_wrap=True, width=16)
    tbl.add_column(no_wrap=True)

    _add_acceleration_row(tbl, metrics)
    _add_time_saved_row(tbl, subtasks, metrics, wall_seconds)
    _add_agents_row(tbl, subtasks)
    _add_tokens_row(tbl, metrics)
    return tbl


def _add_acceleration_row(tbl: Table, metrics: SessionMetrics) -> None:
    if metrics.acceleration_ratio is None:
        return
    ratio = metrics.acceleration_ratio
    style = "bold green" if ratio >= 2.0 else "bold yellow" if ratio >= 1.0 else "red"
    tbl.add_row("Acceleration", Text(f"{ratio:.1f}x faster than sequential", style=style))


def _add_time_saved_row(
    tbl: Table,
    subtasks: dict[str, dict],
    metrics: SessionMetrics,
    wall_seconds: float,
) -> None:
    est_total = sum(metrics.estimated_human_minutes.values())
    if est_total <= 0:
        return
    est_seconds = est_total * 60
    saved = max(0, est_seconds - wall_seconds)
    tbl.add_row(
        "Time Saved",
        Text(
            f"{est_total}m estimated \u2192 {_format_duration(wall_seconds)} actual"
            f" (saved ~{_format_duration(saved)})"
        ),
    )


def _add_agents_row(tbl: Table, subtasks: dict[str, dict]) -> None:
    total = len(subtasks)
    if total == 0:
        return
    counts = _count_statuses(subtasks)
    done = counts.get("done", 0)
    errors = counts.get("error", 0)
    parts = [f"{done}/{total} succeeded"]
    if errors:
        parts.append(f"{errors} error")
    max_concurrent = _max_concurrent(subtasks)
    if total > 1 and max_concurrent > 1:
        parts.append(f"max {max_concurrent} concurrent")
    tbl.add_row("Agents", Text(" \u00b7 ".join(parts)))


def _add_tokens_row(tbl: Table, metrics: SessionMetrics) -> None:
    total = metrics.total_input_tokens + metrics.total_output_tokens
    if total == 0:
        return
    tbl.add_row(
        "Tokens",
        Text(
            f"{_format_tokens(metrics.total_input_tokens)} input"
            f" \u00b7 {_format_tokens(metrics.total_output_tokens)} output"
        ),
    )


def _build_breakdown_table(subtasks: dict[str, dict], metrics: SessionMetrics) -> Table:
    """Per-task breakdown table with estimated vs actual time."""
    tbl = Table(
        show_header=True,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
        expand=False,
        header_style="dim bold",
    )
    tbl.add_column("", width=2, no_wrap=True)
    tbl.add_column("Task", no_wrap=True, max_width=_MAX_TASK_NAME)
    tbl.add_column("Agent", no_wrap=True, max_width=14)
    tbl.add_column("Est", justify="right", no_wrap=True, width=5)
    tbl.add_column("Actual", justify="right", no_wrap=True, width=6)

    for name, info in subtasks.items():
        status = info.get("status", "pending")
        badge_char, badge_style = _STATUS_BADGES.get(status, ("\u25cb", "dim"))
        badge = Text(badge_char, style=badge_style)
        row_style = _ROW_STYLES.get(status, "")
        agent = Text(info.get("agent", ""), style=row_style)
        name_text = Text(_truncate(name, _MAX_TASK_NAME), style=row_style)
        est_min = metrics.estimated_human_minutes.get(name)
        est_str = f"{est_min}m" if est_min is not None else "\u2014"
        actual = _elapsed_since(info.get("started_at"), info.get("finished_at"))
        tbl.add_row(badge, name_text, agent, est_str, actual or "\u2014")

    return tbl


def _count_statuses(subtasks: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for info in subtasks.values():
        st = info.get("status", "pending")
        counts[st] = counts.get(st, 0) + 1
    return counts


def _max_concurrent(subtasks: dict[str, dict]) -> int:
    """Compute maximum number of agents running at the same time."""
    events: list[tuple[float, int]] = []
    for info in subtasks.values():
        start = info.get("started_at")
        end = info.get("finished_at")
        if start is not None:
            events.append((start, 1))
            events.append((end if end is not None else time.monotonic(), -1))
    events.sort()
    peak, current = 0, 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


class RunDashboard:
    """Compact bottom status bar rendered via Rich Live.

    The Live widget is lazy — it only starts when there is at least one subtask.
    Before that, a lightweight "connecting" spinner is shown during SDK warmup.
    """

    def __init__(self, goal: str, verbose: bool = False) -> None:
        self.goal = goal[:60]
        self.verbose = verbose
        self.start_time = time.monotonic()
        self.phase = "starting"
        self.subtasks: dict[str, dict] = {}
        self.pane_activity: dict[int, str] = {}
        self.dispatch_prompts: dict[str, str] = {}
        self.tool_events: list[dict] = []
        self.phase_times: dict[str, tuple[float, float | None]] = {}
        self.metrics = SessionMetrics()
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
                refresh_per_second=20,
                transient=True,
            )
            self._spinner_live.start()

    @staticmethod
    def _spinner_renderable(label: str):
        from rich.columns import Columns
        from rich.spinner import Spinner

        return Columns(
            [Spinner("dots2", style="dim cyan"), Text(label, style="dim")], padding=(0, 1)
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
            refresh_per_second=8,
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
        now = time.monotonic()
        if old_phase and old_phase in self.phase_times:
            start, _ = self.phase_times[old_phase]
            self.phase_times[old_phase] = (start, now)
        if phase not in self.phase_times:
            self.phase_times[phase] = (now, None)
        if phase != old_phase and self._last_phase != phase:
            self._last_phase = phase
            if self._live is not None:
                self._live.update(Text(""))
                self._live.stop()
                self._live = None
            print_phase_divider(phase)
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
        estimated_minutes: int | None = None,
    ) -> None:
        existing = self.subtasks.get(name, {})
        mono_started = existing.get("started_at")
        mono_finished = existing.get("finished_at")
        now_mono = time.monotonic()
        if started_at is not None and mono_started is None:
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
        if estimated_minutes is not None:
            self.metrics.estimated_human_minutes[name] = estimated_minutes
        self._ensure_live()
        self._refresh()

    def set_session_metrics(
        self,
        *,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        acceleration_ratio: float | None = None,
        critical_path_seconds: float | None = None,
    ) -> None:
        """Update session-level metrics for the done banner."""
        self.metrics.total_input_tokens = total_input_tokens
        self.metrics.total_output_tokens = total_output_tokens
        self.metrics.acceleration_ratio = acceleration_ratio
        self.metrics.critical_path_seconds = critical_path_seconds
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

    def set_dispatch_prompt(self, name: str, prompt: str) -> None:
        first_line = prompt.split("\n", 1)[0].strip()
        self.dispatch_prompts[name] = first_line

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
        tbl = self._build_subtask_table()
        progress, done, total = self._build_progress_line()
        parts: list[ConsoleRenderable] = [tbl, progress]

        phase_row = self._render_phase_durations()
        if phase_row:
            parts.append(phase_row)

        all_done = done == total > 0
        if all_done:
            parts.append(self._render_done_banner())

        border = "green" if all_done else "dim"
        return Panel(
            Group(*parts),
            title="[bold]agents[/bold]",
            title_align="left",
            border_style=border,
            padding=(0, 1),
        )

    def _activity_max_width(self) -> int:
        """Responsive activity column width based on terminal size."""
        width = console.size.width if console.size else 100
        if width < 100:
            return 30
        if width > 160:
            return 80
        return 60

    def _build_subtask_table(self) -> Table:
        activity_width = self._activity_max_width()
        tbl = Table(
            show_header=False,
            show_edge=False,
            pad_edge=False,
            padding=(0, 1),
            expand=False,
        )
        tbl.add_column(width=2, no_wrap=True)
        tbl.add_column(style="bold", no_wrap=True, max_width=_MAX_TASK_NAME)
        tbl.add_column(style="dim", no_wrap=True, max_width=14)
        tbl.add_column(style="dim", no_wrap=True, max_width=activity_width)
        if self.verbose:
            tbl.add_column(style="dim", justify="right", no_wrap=True, width=5)
        tbl.add_column(style="dim", justify="right", no_wrap=True, width=6)

        sorted_items = sorted(
            self.subtasks.items(),
            key=lambda kv: _STATUS_SORT_ORDER.get(kv[1].get("status", "pending"), 9),
        )
        prev_group: int | None = None
        for name, info in sorted_items:
            group = _STATUS_SORT_ORDER.get(info.get("status", "pending"), 9)
            if prev_group is not None and group != prev_group and group >= 2:
                col_count = 6 if self.verbose else 5
                tbl.add_row(*[Text("")] * col_count)
            prev_group = group
            if self.verbose:
                self._add_verbose_row(tbl, name, info)
            else:
                self._add_compact_row(tbl, name, info, activity_width)
        return tbl

    def _add_compact_row(self, tbl: Table, name: str, info: dict, activity_width: int) -> None:
        badge, name_text, agent = self._base_row_cells(name, info)
        status = info.get("status", "pending")
        style = _ROW_STYLES.get(status, "")
        activity = Text(self._task_activity(name, info, activity_width), style=style)
        elapsed = _elapsed_since(info.get("started_at"), info.get("finished_at"))
        tbl.add_row(badge, name_text, agent, activity, elapsed)
        if status == "error":
            self._add_error_detail(tbl, info)

    def _add_verbose_row(self, tbl: Table, name: str, info: dict) -> None:
        badge, name_text, agent = self._base_row_cells(name, info)
        status = info.get("status", "pending")
        style = _ROW_STYLES.get(status, "")
        activity_width = self._activity_max_width()
        activity = Text(self._task_activity(name, info, activity_width), style=style)
        pane_str = f"#{info['pane_id']}" if info.get("pane_id") is not None else ""
        elapsed = _elapsed_since(info.get("started_at"), info.get("finished_at"))
        tbl.add_row(badge, name_text, agent, activity, pane_str, elapsed)
        prompt_line = self.dispatch_prompts.get(name)
        if prompt_line and status in ("running", "done"):
            detail = Text(f"  {_truncate(prompt_line, 60)}", style="dim italic")
            tbl.add_row(Text(""), detail)
        if status == "error":
            self._add_error_detail(tbl, info)

    def _base_row_cells(self, name: str, info: dict) -> tuple[Text, Text, Text]:
        status = info.get("status", "pending")
        badge_char, badge_style = _STATUS_BADGES.get(status, ("\u25cb", "dim"))
        row_style = _ROW_STYLES.get(status, "")
        return (
            Text(badge_char, style=badge_style),
            Text(_truncate(name, _MAX_TASK_NAME), style=row_style),
            Text(info.get("agent", ""), style=row_style),
        )

    def _add_error_detail(self, tbl: Table, info: dict) -> None:
        """Add expanded error detail lines below an error row."""
        error_text = info.get("error") or info.get("last_output") or ""
        if not error_text:
            pane_id = info.get("pane_id")
            if pane_id is not None:
                error_text = self.pane_activity.get(pane_id, "")
        if not error_text:
            return
        lines = error_text.strip().splitlines()[:3]
        for line in lines:
            detail = Text(f"    {_truncate(line.strip(), 80)}", style="dim red")
            tbl.add_row(Text(""), detail)

    def _task_activity(self, name: str, info: dict, max_len: int = 60) -> str:
        status = info.get("status", "pending")
        if status == "done":
            return "done"
        if status == "pending":
            return ""
        pane_id = info.get("pane_id")
        if pane_id is not None and pane_id in self.pane_activity:
            return _truncate(self.pane_activity[pane_id], max_len)
        return ""

    def _build_progress_line(self) -> tuple[Text, int, int]:
        elapsed = _elapsed(self.start_time)
        total = len(self.subtasks)
        counts = _count_statuses(self.subtasks)
        done = counts.get("done", 0)

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

        return progress, done, total

    def _render_phase_durations(self) -> Text | None:
        if not self.phase_times:
            return None
        now = time.monotonic()
        segments = []
        for phase, (start, end) in self.phase_times.items():
            secs = int((end or now) - start)
            segments.append(f"{phase}: {secs}s")
        line = Text("  ")
        line.append(" | ".join(segments), style="dim")
        return line

    def _render_done_banner(self) -> ConsoleRenderable:
        elapsed = _elapsed(self.start_time)
        wall = time.monotonic() - self.start_time
        parts: list[ConsoleRenderable] = []

        # Summary metrics line
        summary = Text("  ")
        summary.append("\u2714 ALL DONE", style="bold green")
        summary.append(f"  {elapsed}", style="dim")
        if self.metrics.acceleration_ratio is not None:
            summary.append(f"  {self.metrics.acceleration_ratio:.1f}x", style="bold cyan")
        parts.append(summary)

        # Detailed metrics (only when we have meaningful data)
        detail_lines = self._done_detail_lines(wall)
        for line in detail_lines:
            parts.append(line)

        if len(parts) == 1:
            return parts[0]
        return Group(*parts)

    def _done_detail_lines(self, wall_seconds: float) -> list[Text]:
        lines: list[Text] = []
        m = self.metrics

        # Time saved
        est_total = sum(m.estimated_human_minutes.values())
        if est_total > 0:
            saved = max(0, est_total * 60 - wall_seconds)
            line = Text("  ")
            line.append(
                f"saved ~{_format_duration(saved)}"
                f" ({est_total}m est \u2192 {_format_duration(wall_seconds)})",
                style="dim",
            )
            lines.append(line)

        # Tokens
        total_tokens = m.total_input_tokens + m.total_output_tokens
        if total_tokens > 0:
            line = Text("  ")
            line.append(
                f"tokens: {_format_tokens(m.total_input_tokens)} in"
                f" \u00b7 {_format_tokens(m.total_output_tokens)} out",
                style="dim",
            )
            lines.append(line)

        # Concurrency
        total = len(self.subtasks)
        if total > 1:
            peak = _max_concurrent(self.subtasks)
            if peak > 1:
                line = Text("  ")
                line.append(f"peak concurrency: {peak} agents", style="dim")
                lines.append(line)

        return lines

    def _refresh(self) -> None:
        if self._live is not None and self._active:
            self._live.update(self._render())


def create_dashboard(
    goal: str,
    verbose: bool = False,
    tui: bool = True,
) -> DashboardProtocol:
    """Factory: return TUI dashboard when available, else classic Rich bar."""
    if tui and sys.stdout.isatty():
        try:
            from openmax.tui import TuiDashboard

            return TuiDashboard(goal, verbose=verbose)
        except ImportError:
            pass
    return RunDashboard(goal, verbose=verbose)
