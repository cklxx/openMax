"""Shared console output for openMax."""

import sys

from rich.console import Console

console = Console()
P = "\u279c"  # ➜


def clear_screen() -> None:
    """Scroll old content up, cursor at top — history preserved in scrollback."""
    sys.stdout.write("\n" * 40 + "\033[H")
    sys.stdout.flush()
