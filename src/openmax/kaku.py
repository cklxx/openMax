"""Kaku terminal integration — thin convenience wrappers.

Most pane management goes through PaneManager.
This module provides standalone utility functions for quick operations.
"""

from __future__ import annotations

import subprocess


def get_current_pane_id() -> int | None:
    """Get the pane ID of the current terminal (from WEZTERM_PANE env)."""
    import os
    pane = os.environ.get("WEZTERM_PANE")
    return int(pane) if pane else None


def is_kaku_available() -> bool:
    """Check if the kaku CLI is available."""
    try:
        result = subprocess.run(
            ["kaku", "cli", "list"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
