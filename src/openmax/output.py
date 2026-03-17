"""Shared console output for openMax."""

from rich.console import Console

console = Console()
P = "\u279c"  # ➜


def clear_screen() -> None:
    """Clear terminal and move cursor to top — vite-style fresh start."""
    console.print("\033[2J\033[H", end="")
