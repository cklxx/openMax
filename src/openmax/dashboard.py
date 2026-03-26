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

from openmax.formatting import (
    _ACCESSIBLE_LABELS,
    estimate_cost_usd,
    format_cost,
    format_tokens_short,
    is_accessible_mode,
)
from openmax.output import console
from openmax.theme import get_theme

_MAX_TOOL_EVENTS = 8
_MAX_TASK_NAME = 24

# Sort priority: running first, pending, done, error last.
_STATUS_SORT_ORDER: dict[str, int] = {
    "running": 0,
    "pending": 1,
    "done": 2,
    "error": 3,
}


def _badge_width() -> int:
    return 6 if is_accessible_mode() else 2


def _status_badges() -> dict[str, tuple[str, str]]:
    t = get_theme()
    accessible = is_accessible_mode()
    badges: dict[str, tuple[str, str]] = {
        "running": ("\u25c9", t.status_running),
        "done": ("\u2714", t.status_done),
        "error": ("\u2718", t.status_error),
        "pending": ("\u25cb", t.status_pending),
    }
    if not accessible:
        return badges
    return {
        k: (f"{icon} {_ACCESSIBLE_LABELS.get(k, '')}", style) for k, (icon, style) in badges.items()
    }


def _row_styles() -> dict[str, str]:
    t = get_theme()
    return {
        "running": t.row_running,
        "done": t.row_done,
        "error": t.row_error,
        "pending": t.row_pending,
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

    def update_task_progress(self, name: str, pct: int) -> None: ...
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
    return _format_duration(time.monotonic() - start)


def _elapsed_since(start: float | None, end: float | None = None) -> str:
    """Elapsed time from a monotonic timestamp to now (or to end)."""
    if start is None:
        return ""
    ref = end if end is not None else time.monotonic()
    return _format_duration(ref - start)


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration: 45s / 2m 30s / 1h 05m."""
    secs = max(0, int(seconds))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m, s = divmod(secs, 60)
        return f"{m}m {s:02d}s"
    h, remainder = divmod(secs, 3600)
    m = remainder // 60
    return f"{h}h {m:02d}m"


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


def _render_progress_bar(pct: int | None, status: str) -> str:
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


def print_phase_divider(phase: str) -> None:
    """Print a styled phase divider to the console."""
    console.print()
    console.print(Rule(f" {phase} ", style=get_theme().phase_rule, align="left"))
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
        border_style=get_theme().summary_border,
        padding=(0, 1),
    )


def _build_metrics_table(
    subtasks: dict[str, dict],
    metrics: SessionMetrics,
    wall_seconds: float,
) -> Table:
    """Build the top-level metrics key-value table."""
    tbl = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    tbl.add_column(style=get_theme().summary_metric_label, no_wrap=True, width=16)
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
    t = get_theme()
    style = t.accel_fast if ratio >= 2.0 else t.accel_medium if ratio >= 1.0 else t.accel_slow
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
    cost = estimate_cost_usd(metrics.total_input_tokens, metrics.total_output_tokens)
    tbl.add_row(
        "Tokens",
        Text(
            f"{_format_tokens(metrics.total_input_tokens)} input"
            f" \u00b7 {_format_tokens(metrics.total_output_tokens)} output"
            f" \u00b7 {format_cost(cost)}"
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
        header_style=get_theme().header_breakdown,
    )
    tbl.add_column("", width=_badge_width(), no_wrap=True)
    tbl.add_column("Task", no_wrap=True, max_width=_MAX_TASK_NAME)
    tbl.add_column("Agent", no_wrap=True, max_width=14)
    tbl.add_column("Est", justify="right", no_wrap=True, width=5)
    tbl.add_column("Actual", justify="right", no_wrap=True, width=6)

    for name, info in subtasks.items():
        status = info.get("status", "pending")
        badge_char, badge_style = _status_badges().get(status, ("\u25cb", "dim"))
        badge = Text(badge_char, style=badge_style)
        row_style = _row_styles().get(status, "")
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
        self.start_time = time.monotonic()
        self.phase = "starting"
        self.subtasks: dict[str, dict] = {}
        self.pane_activity: dict[int, str] = {}
        self.task_progress: dict[str, int] = {}
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

        t = get_theme()
        return Columns(
            [Spinner("dots2", style=t.spinner_style), Text(label, style=t.spinner_label)],
            padding=(0, 1),
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

    def update_task_progress(self, name: str, pct: int) -> None:
        self.task_progress[name] = max(0, min(100, pct))
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
        if len(self.subtasks) == 0:
            return self._render_simple()
        return self._render_full()

    def _render_simple(self) -> Text:
        """Minimal status line when no subtasks exist yet."""
        elapsed = _elapsed(self.start_time)
        t = get_theme()
        line = Text()
        line.append(f"  {self.phase}", style=t.status_phase)
        line.append(f"  {elapsed}", style=t.status_elapsed)
        return line

    def _render_full(self) -> ConsoleRenderable:
        """Tree-style status display matching Claude Code's agent layout."""
        counts = _count_statuses(self.subtasks)
        done = counts.get("done", 0) + counts.get("error", 0)
        total = len(self.subtasks)
        all_done = done == total > 0

        parts: list[ConsoleRenderable] = []
        parts.append(self._build_header_line(counts, total))
        parts.extend(self._build_tree_lines())
        parts.append(self._build_summary_line(done, total, all_done))
        if all_done:
            parts.append(self._render_done_banner())
        return Group(*parts)

    def _build_header_line(self, counts: dict[str, int], total: int) -> Text:
        t = get_theme()
        running = counts.get("running", 0)
        line = Text()
        if running > 0:
            line.append(f"  Running {running} agent", style=t.tree_header)
            line.append("s" if running != 1 else "", style=t.tree_header)
            line.append("\u2026", style=t.tree_header)
        else:
            line.append(f"  {total} agents", style=t.tree_header)
        return line

    def _build_tree_lines(self) -> list[Text]:
        sorted_items = sorted(
            self.subtasks.items(),
            key=lambda kv: _STATUS_SORT_ORDER.get(kv[1].get("status", "pending"), 9),
        )
        lines: list[Text] = []
        activity_width = self._activity_max_width()
        for i, (name, info) in enumerate(sorted_items):
            is_last = i == len(sorted_items) - 1
            lines.append(self._build_agent_line(name, info, is_last))
            activity_line = self._build_activity_line(name, info, is_last, activity_width)
            if activity_line:
                lines.append(activity_line)
        return lines

    def _build_agent_line(self, name: str, info: dict, is_last: bool) -> Text:
        t = get_theme()
        status = info.get("status", "pending")
        connector = "   \u2514\u2500 " if is_last else "   \u251c\u2500 "
        badge_char, badge_style = _status_badges().get(status, ("\u25cb", "dim"))
        row_style = _row_styles().get(status, "")

        line = Text()
        line.append(connector, style=t.tree_connector)
        if is_accessible_mode():
            label = _ACCESSIBLE_LABELS.get(status, "")
            line.append(f"{badge_char} {label} ", style=badge_style)
        else:
            line.append(f"{badge_char} ", style=badge_style)
        line.append(_truncate(name, _MAX_TASK_NAME), style=row_style)
        line.append(f" \u00b7 {info.get('agent', '')}", style=t.col_secondary)
        elapsed = _elapsed_since(info.get("started_at"), info.get("finished_at"))
        if elapsed:
            line.append(f" \u00b7 {elapsed}", style=t.col_secondary)
        return line

    def _build_activity_line(
        self,
        name: str,
        info: dict,
        is_last: bool,
        max_len: int,
    ) -> Text | None:
        status = info.get("status", "pending")
        activity = self._task_activity(name, info, max_len)
        error_text = self._error_text(info) if status == "error" else ""
        text = error_text or activity
        if not text:
            return None
        t = get_theme()
        pipe = "      " if is_last else "   \u2502  "
        style = t.error_detail if status == "error" else t.col_secondary
        line = Text()
        line.append(pipe, style=t.tree_connector)
        line.append(f"\u23bf  {_truncate(text, max_len)}", style=style)
        return line

    def _activity_max_width(self) -> int:
        width = console.size.width if console.size else 100
        if width < 100:
            return 30
        if width > 160:
            return 80
        return 60

    def _task_activity(self, name: str, info: dict, max_len: int = 60) -> str:
        status = info.get("status", "pending")
        if status in ("done", "pending"):
            return ""
        pane_id = info.get("pane_id")
        if pane_id is not None and pane_id in self.pane_activity:
            return _truncate(self.pane_activity[pane_id], max_len)
        return ""

    def _error_text(self, info: dict) -> str:
        text = info.get("error") or info.get("last_output") or ""
        if not text:
            pane_id = info.get("pane_id")
            if pane_id is not None:
                text = self.pane_activity.get(pane_id, "")
        first_line = text.strip().split("\n", 1)[0].strip() if text else ""
        return first_line

    def _build_summary_line(
        self,
        done: int,
        total: int,
        all_done: bool,
    ) -> ConsoleRenderable:
        t = get_theme()
        elapsed = _elapsed(self.start_time)
        counts = _count_statuses(self.subtasks)

        line = Text("   ")
        parts: list[str] = [f"{done}/{total}"]
        if counts.get("running"):
            parts.append(f"{counts['running']} running")
        if counts.get("error"):
            parts.append(f"{counts['error']} err")
        if counts.get("pending"):
            parts.append(f"{counts['pending']} queued")
        line.append(" \u00b7 ".join(parts), style=t.tree_summary)

        eta = self._estimate_eta(done, total)
        if eta is not None:
            line.append(f"  ETA ~{_format_duration(eta)}", style=t.progress_eta)

        line.append(f"  {elapsed}", style=t.progress_elapsed)

        if self._monitor_count > 0:
            line.append(f"  [{self._monitor_count} checks]", style=t.tree_summary)

        total_tokens = self.metrics.total_input_tokens + self.metrics.total_output_tokens
        if total_tokens > 0:
            cost = estimate_cost_usd(
                self.metrics.total_input_tokens, self.metrics.total_output_tokens
            )
            tok_str = format_tokens_short(total_tokens)
            line.append(f"  {tok_str} tokens", style=t.tree_summary)
            line.append(f" \u00b7 {format_cost(cost)}", style=t.tree_summary)

        if not all_done:
            return self._with_spinner(line)
        return line

    def _with_spinner(self, text: Text) -> ConsoleRenderable:
        """Prepend a Rich Spinner to the progress line."""
        from rich.columns import Columns
        from rich.spinner import Spinner

        return Columns([Spinner("dots", style=get_theme().progress_spinner), text], padding=(0, 0))

    def _estimate_eta(self, done: int, total: int) -> float | None:
        """Estimate remaining seconds. Returns None if <10% complete."""
        if total == 0 or done == 0:
            return None
        ratio = done / total
        if ratio < 0.1 or ratio >= 1.0:
            return None
        elapsed = time.monotonic() - self.start_time
        return max(0, (elapsed / ratio) - elapsed)

    def _render_phase_durations(self) -> Text | None:
        if not self.phase_times:
            return None
        t = get_theme()
        now = time.monotonic()
        line = Text("  ")
        entries = list(self.phase_times.items())
        for i, (phase, (start, end)) in enumerate(entries):
            is_active = end is None
            duration = _format_duration((end or now) - start)
            style = t.status_phase if is_active else t.banner_detail
            if i > 0:
                line.append(" | ", style=t.banner_detail)
            line.append(f"{phase}: {duration}", style=style)
        return line

    def _render_done_banner(self) -> ConsoleRenderable:
        wall = time.monotonic() - self.start_time
        counts = _count_statuses(self.subtasks)
        total = len(self.subtasks)
        done_count = counts.get("done", 0)
        error_count = counts.get("error", 0)
        has_errors = error_count > 0

        t = get_theme()
        check = "\u2714" if not has_errors else "\u26a0"
        label_style = t.banner_warn_label if has_errors else t.banner_done_label

        parts: list[str] = [f"{done_count}/{total} tasks", _format_duration(wall)]

        total_tokens = self.metrics.total_input_tokens + self.metrics.total_output_tokens
        if total_tokens > 0:
            cost = estimate_cost_usd(
                self.metrics.total_input_tokens, self.metrics.total_output_tokens
            )
            parts.append(format_cost(cost))

        if self.metrics.acceleration_ratio is not None:
            parts.append(f"{self.metrics.acceleration_ratio:.1f}x")

        if has_errors:
            parts.append(f"{error_count} error{'s' if error_count > 1 else ''}")

        headline = Text()
        headline.append(f" {check} ", style=label_style)
        headline.append(" \u00b7 ".join(parts))

        border = t.status_error if has_errors else t.panel_border_done
        return Panel(
            headline,
            title="[bold]Done[/bold]",
            title_align="left",
            border_style=border,
            padding=(0, 1),
        )

    def _refresh(self) -> None:
        if self._live is not None and self._active:
            self._live.update(self._render())


def create_dashboard(
    goal: str,
    verbose: bool = False,
) -> DashboardProtocol:
    """Create a classic Rich status-bar dashboard."""
    return RunDashboard(goal, verbose=verbose)
