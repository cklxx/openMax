"""Terminal backend detection, installation, and utilities.

Supports Kaku (macOS) and tmux (cross-platform).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess


def is_tmux_available() -> bool:
    """Check if tmux is installed and usable."""
    return shutil.which("tmux") is not None


def is_in_tmux_session() -> bool:
    """Check if we're currently inside a tmux session."""
    return os.environ.get("TMUX") is not None


def get_current_pane_id() -> int | None:
    """Get the pane ID of the current terminal (from WEZTERM_PANE env)."""
    pane = os.environ.get("WEZTERM_PANE")
    if not pane:
        return None
    try:
        return int(pane)
    except ValueError:
        return None


def is_kaku_available() -> bool:
    """Check if the kaku CLI is available."""
    try:
        result = subprocess.run(
            ["kaku", "cli", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_brew() -> bool:
    """Check if Homebrew is available."""
    try:
        result = subprocess.run(["brew", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
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


def _install_tmux() -> bool:
    """Attempt to install tmux via the system package manager."""
    from rich.console import Console

    console = Console()
    system = platform.system()

    if system == "Darwin" and _has_brew():
        console.print("[cyan]Installing tmux via Homebrew...[/cyan]")
        result = subprocess.run(["brew", "install", "tmux"], timeout=300)
    elif system == "Linux" and shutil.which("apt-get"):
        console.print("[cyan]Installing tmux via apt...[/cyan]")
        result = subprocess.run(["sudo", "apt-get", "install", "-y", "tmux"], timeout=300)
    elif system == "Linux" and shutil.which("dnf"):
        console.print("[cyan]Installing tmux via dnf...[/cyan]")
        result = subprocess.run(["sudo", "dnf", "install", "-y", "tmux"], timeout=300)
    elif system == "Linux" and shutil.which("pacman"):
        console.print("[cyan]Installing tmux via pacman...[/cyan]")
        result = subprocess.run(["sudo", "pacman", "-S", "--noconfirm", "tmux"], timeout=300)
    else:
        return False

    if result.returncode == 0:
        console.print("[green]tmux installed successfully.[/green]")
        return True
    console.print("[red]tmux installation failed.[/red]")
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

    system = platform.system()

    # Non-macOS: suggest tmux instead
    if system != "Darwin":
        console.print(
            "[yellow]Kaku is macOS-only.[/yellow] "
            "Install [bold]tmux[/bold] instead, then run openmax normally."
        )
        return False

    if _has_brew():
        console.print("[yellow]Kaku terminal is required but not installed.[/yellow]")
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
        "Then open Kaku and run openmax from inside it.\n"
        "\n"
        "[dim]Alternatively, install tmux and run openmax inside a tmux session.[/dim]"
    )
    return False


def ensure_tmux() -> bool:
    """Ensure tmux is installed. Auto-install if possible.

    Returns True if tmux is ready to use, False otherwise.
    A tmux session is NOT required — TmuxPaneBackend auto-creates one.
    """
    if is_tmux_available():
        return True

    from rich.console import Console

    console = Console()
    console.print("[yellow]tmux is not installed.[/yellow]")
    try:
        answer = input("Install tmux now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("", "y", "yes"):
        if _install_tmux():
            return True
        _print_tmux_install_guide()
        return False

    _print_tmux_install_guide()
    return False


def _print_tmux_install_guide() -> None:
    """Print manual tmux installation instructions."""
    from rich.console import Console

    console = Console()
    system = platform.system()
    if system == "Darwin":
        install_cmd = "brew install tmux"
    else:
        install_cmd = "apt install tmux  # or: dnf install tmux / pacman -S tmux"

    console.print(
        "[red]tmux is required but not installed.[/red]\n"
        "\n"
        "[bold]Install:[/bold]\n"
        f"  {install_cmd}\n"
        "\n"
        "Then run openmax:\n"
        '  openmax run "your task"'
    )
