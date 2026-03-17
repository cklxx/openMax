"""Shared console output for openMax."""

import sys

from rich.console import Console

console = Console()
P = "\u279c"  # ➜


def clear_screen() -> None:
    """Clear terminal and move cursor to top — vite-style fresh start."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
