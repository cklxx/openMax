"""CLI entry point for openMax."""

from __future__ import annotations

import atexit
import os
import signal
import sys

import click
from rich.console import Console

from openmax.kaku import ensure_kaku, is_kaku_available
from openmax.lead_agent import run_lead_agent
from openmax.pane_manager import PaneManager

console = Console()


@click.group()
@click.version_option(version=None, package_name="openmax", prog_name="openmax")
def main() -> None:
    """openMax — Multi AI Agent orchestration hub."""


@main.command()
@click.argument("task")
@click.option("--cwd", default=None, help="Working directory for agents")
@click.option("--model", default=None, help="Model for the lead agent")
@click.option("--max-turns", default=50, type=int, help="Max agent loop turns")
@click.option("--keep-panes", is_flag=True, default=False, help="Don't close panes on exit")
def run(task: str, cwd: str | None, model: str | None, max_turns: int, keep_panes: bool) -> None:
    """Decompose TASK and dispatch sub-agents in Kaku panes."""
    if cwd is None:
        cwd = os.getcwd()

    if not ensure_kaku():
        raise SystemExit(1)

    pane_mgr = PaneManager()

    # Safety net: atexit ensures cleanup even on unhandled exceptions
    _cleaned_up = False

    def _do_cleanup():
        nonlocal _cleaned_up
        if _cleaned_up or keep_panes:
            return
        _cleaned_up = True
        try:
            pane_mgr.cleanup_all()
        except Exception:
            pass  # best-effort at exit

    atexit.register(_do_cleanup)

    def _cleanup_and_exit(signum, frame):
        console.print("\n[yellow]Interrupted — cleaning up panes...[/yellow]")
        _do_cleanup()
        console.print("[green]All managed panes closed.[/green]")
        sys.exit(130 if signum == signal.SIGINT else 143)

    signal.signal(signal.SIGINT, _cleanup_and_exit)
    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    try:
        plan = run_lead_agent(
            task=task,
            pane_mgr=pane_mgr,
            cwd=cwd,
            model=model,
            max_turns=max_turns,
        )

        # Session complete — show final summary before cleanup
        summary = pane_mgr.summary()
        console.print(
            f"\n[bold green]Done.[/bold green] "
            f"{len(plan.subtasks)} sub-tasks | "
            f"{summary['total_windows']} windows | "
            f"{summary['done']} done"
        )
    finally:
        if not keep_panes:
            console.print("[dim]Closing managed panes...[/dim]")
            _do_cleanup()
            console.print("[green]All managed panes closed.[/green]")
        else:
            console.print("[dim]Keeping panes open (--keep-panes).[/dim]")


@main.command()
def panes() -> None:
    """List all kaku panes."""
    if not is_kaku_available():
        console.print("[red]kaku CLI not available.[/red]")
        raise SystemExit(1)

    all_panes = PaneManager.list_all_panes()
    console.print(f"[bold]Found {len(all_panes)} panes:[/bold]")
    for p in all_panes:
        active = " [green]★[/green]" if p.is_active else ""
        console.print(
            f"  Pane {p.pane_id}: {p.title or '(untitled)'} "
            f"[dim]({p.cols}x{p.rows})[/dim]{active}"
        )


@main.command("read-pane")
@click.argument("pane_id", type=int)
def read_pane(pane_id: int) -> None:
    """Read the text content of a specific pane."""
    mgr = PaneManager()
    try:
        text = mgr.get_text(pane_id)
        console.print(text)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
