"""Shared formatting utilities for CLI output."""

from __future__ import annotations

from datetime import datetime, timezone

from openmax.theme import get_theme


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
    """Return a Rich-markup status icon: ✔ green, ✘ red, ● cyan, etc."""
    if not status:
        fallback = get_theme().icon_pending
        return f"[{fallback}]\u25cb[/{fallback}]"
    icon, style = _status_icons().get(status.lower(), ("\u25cb", get_theme().icon_pending))
    return f"[{style}]{icon}[/{style}]"


def status_icon_plain(status: str | None) -> str:
    """Return a plain status icon without Rich markup."""
    if not status:
        return "\u25cb"
    icon, _ = _status_icons().get(status.lower(), ("\u25cb", ""))
    return icon
