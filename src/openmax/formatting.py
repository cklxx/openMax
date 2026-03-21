"""Shared formatting utilities for CLI output."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from openmax.theme import get_theme


def is_accessible_mode() -> bool:
    """Check if accessible mode is enabled via OPENMAX_ACCESSIBLE=1."""
    return os.environ.get("OPENMAX_ACCESSIBLE", "0") == "1"


def format_relative_time(value: str | None) -> str:
    """Format an ISO timestamp as relative time: '2h ago', 'yesterday', 'Mar 15'."""
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return value if value else "-"
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 172800:
        return "yesterday"
    if secs < 604800:
        return f"{secs // 86400}d ago"
    return dt.strftime("%b %d")


def format_tokens(count: int | None) -> str:
    """Format token count with k/M suffix: 1234 -> '1.2k', 1500000 -> '1.5M'."""
    if count is None or count < 0:
        return "-"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


_ACCESSIBLE_LABELS: dict[str, str] = {
    "done": "DONE",
    "completed": "DONE",
    "running": "RUN",
    "active": "RUN",
    "pending": "WAIT",
    "error": "FAIL",
    "failed": "FAIL",
    "partial": "PART",
    "aborted": "STOP",
}


def _status_icons() -> dict[str, tuple[str, str]]:
    t = get_theme()
    return {
        "done": ("\u2714", t.icon_done),
        "completed": ("\u2714", t.icon_completed),
        "running": ("\u25cf", t.icon_running),
        "active": ("\u25cf", t.icon_active),
        "pending": ("\u25cb", t.icon_pending),
        "error": ("\u2718", t.icon_error),
        "failed": ("\u2718", t.icon_failed),
        "partial": ("\u25d0", t.icon_partial),
        "aborted": ("\u25cb", t.icon_aborted),
    }


def status_icon(status: str | None) -> str:
    """Return a Rich-markup status icon: ✔ green, ✘ red, ● cyan, etc.

    When OPENMAX_ACCESSIBLE=1, appends a text label (e.g. "● RUN").
    """
    if not status:
        fallback = get_theme().icon_pending
        label = " WAIT" if is_accessible_mode() else ""
        return f"[{fallback}]\u25cb{label}[/{fallback}]"
    key = status.lower()
    icon, style = _status_icons().get(key, ("\u25cb", get_theme().icon_pending))
    label = f" {_ACCESSIBLE_LABELS.get(key, '')}" if is_accessible_mode() else ""
    return f"[{style}]{icon}{label}[/{style}]"


def status_icon_plain(status: str | None) -> str:
    """Return a plain status icon without Rich markup.

    When OPENMAX_ACCESSIBLE=1, appends a text label.
    """
    if not status:
        return "\u25cb WAIT" if is_accessible_mode() else "\u25cb"
    key = status.lower()
    icon, _ = _status_icons().get(key, ("\u25cb", ""))
    if is_accessible_mode():
        return f"{icon} {_ACCESSIBLE_LABELS.get(key, '')}"
    return icon
