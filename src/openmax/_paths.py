"""Shared path helpers and time utilities."""

from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_sessions_dir() -> Path:
    return Path.home() / ".openmax" / "sessions"
