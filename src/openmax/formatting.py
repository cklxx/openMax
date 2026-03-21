"""Shared formatting utilities for CLI output."""

from __future__ import annotations

from datetime import datetime, timezone


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


_STATUS_ICONS: dict[str, tuple[str, str]] = {
    "done": ("✔", "green"),
    "completed": ("✔", "green"),
    "running": ("●", "cyan"),
    "active": ("●", "yellow"),
    "pending": ("○", "dim"),
    "error": ("✘", "red"),
    "failed": ("✘", "red"),
    "partial": ("◐", "yellow"),
    "aborted": ("○", "dim"),
}


def status_icon(status: str | None) -> str:
    """Return a Rich-markup status icon: ✔ green, ✘ red, ● cyan, etc."""
    if not status:
        return "[dim]○[/dim]"
    icon, style = _STATUS_ICONS.get(status.lower(), ("○", "dim"))
    return f"[{style}]{icon}[/{style}]"


def status_icon_plain(status: str | None) -> str:
    """Return a plain status icon without Rich markup."""
    if not status:
        return "○"
    icon, _ = _STATUS_ICONS.get(status.lower(), ("○", "dim"))
    return icon
