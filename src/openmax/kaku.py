"""Kaku terminal integration — detection, installation, and utilities."""

from __future__ import annotations

import os
import platform
import subprocess
import sys


def get_current_pane_id() -> int | None:
    """Get the pane ID of the current terminal (from WEZTERM_PANE env)."""
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


def _has_brew() -> bool:
    """Check if Homebrew is available."""
    try:
        subprocess.run(["brew", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _install_kaku_brew() -> bool:
    """Install Kaku via Homebrew cask."""
    from rich.console import Console
    console = Console()
    console.print("[cyan]Installing Kaku via Homebrew...[/cyan]")
    result = subprocess.run(
        ["brew", "install", "--cask", "kaku"],
        timeout=300,
    )
    if result.returncode == 0:
        console.print("[green]Kaku installed successfully.[/green]")
        return True
    console.print("[red]Kaku installation failed.[/red]")
    return False


def ensure_kaku() -> bool:
    """Ensure Kaku is available. Auto-install if possible, or guide the user.

    Returns True if kaku is ready to use, False otherwise.
    """
    if is_kaku_available():
        return True

    from rich.console import Console
    console = Console()

    # Check if kaku binary exists but we're not inside a kaku terminal
    kaku_exists = False
    try:
        subprocess.run(["kaku", "--version"], capture_output=True, timeout=5)
        kaku_exists = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if kaku_exists:
        console.print(
            "[yellow]Kaku is installed but `kaku cli` is not responding.[/yellow]\n"
            "[yellow]Make sure you're running openmax inside a Kaku terminal window.[/yellow]"
        )
        return False

    # Kaku not installed — try to auto-install (macOS only)
    system = platform.system()

    if system != "Darwin":
        console.print(
            "[red]Kaku terminal is currently macOS only.[/red]\n"
            "See https://github.com/niceda/kaku for updates."
        )
        return False

    if _has_brew():
        console.print(
            "[yellow]Kaku terminal is required but not installed.[/yellow]"
        )
        try:
            answer = input("Install Kaku via Homebrew? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("", "y", "yes"):
            if _install_kaku_brew():
                console.print(
                    "\n[green]Kaku installed![/green] "
                    "Please open Kaku and run openmax again from inside it."
                )
            return False

    console.print(
        "[red]Kaku terminal is required but not installed.[/red]\n"
        "\n"
        "[bold]Install:[/bold]\n"
        "  brew install --cask kaku\n"
        "  [dim]or download from[/dim] https://github.com/niceda/kaku\n"
        "\n"
        "Then open Kaku and run openmax from inside it."
    )
    return False
