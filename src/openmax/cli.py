"""CLI entry point for openMax."""

from __future__ import annotations

import os

import click
from rich.console import Console

from openmax.kaku import is_kaku_available
from openmax.lead_agent import run_lead_agent
from openmax.pane_manager import PaneManager

console = Console()


@click.group()
def main() -> None:
    """openMax — Multi AI Agent orchestration hub."""


@main.command()
@click.argument("task")
@click.option("--cwd", default=None, help="Working directory for agents")
@click.option("--model", default=None, help="Model for the lead agent")
@click.option("--max-turns", default=50, type=int, help="Max agent loop turns")
def run(task: str, cwd: str | None, model: str | None, max_turns: int) -> None:
    """Decompose TASK and dispatch sub-agents in Kaku panes."""
    if cwd is None:
        cwd = os.getcwd()

    if not is_kaku_available():
        console.print("[red]Error: kaku CLI not available. Run inside Kaku terminal.[/red]")
        raise SystemExit(1)

    plan = run_lead_agent(task=task, cwd=cwd, model=model, max_turns=max_turns)

    console.print(f"\n[bold green]Done.[/bold green] {len(plan.subtasks)} sub-tasks dispatched.")


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
