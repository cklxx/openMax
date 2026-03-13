"""CLI entry point for openMax."""

from __future__ import annotations

import atexit
import os
import signal
import sys
from collections import Counter
from datetime import datetime, timezone

import click
from rich.console import Console

from openmax.agent_registry import AgentConfigError, load_agent_registry
from openmax.kaku import ensure_kaku, is_kaku_available
from openmax.lead_agent import LeadAgentStartupError, run_lead_agent
from openmax.memory_system import MemoryStore
from openmax.pane_manager import PaneManager
from openmax.session_runtime import SessionSnapshot, SessionStore

console = Console()
_ANCHOR_PREVIEW_LIMIT = 5


def _resolve_cwd(cwd: str | None) -> str:
    return os.path.realpath(cwd or os.getcwd())


def _parse_allowed_agents(agents: str | None, available_agents: set[str]) -> list[str] | None:
    if not agents:
        return None

    parsed = list(
        dict.fromkeys(agent.strip().lower() for agent in agents.split(",") if agent.strip())
    )
    if not parsed:
        raise click.UsageError("--agents requires at least one agent name")

    unknown = set(parsed) - available_agents
    if unknown:
        raise click.UsageError(
            f"Unknown agent type(s): {', '.join(sorted(unknown))}. "
            f"Valid types: {', '.join(sorted(available_agents))}"
        )
    return parsed


def _format_timestamp(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_completion(value: int | None) -> str:
    return f"{value}%" if value is not None else "n/a"


def _render_subtask_counts(snapshot: SessionSnapshot) -> str:
    counts = Counter(task.status for task in snapshot.plan.subtasks)
    parts = [f"{len(snapshot.plan.subtasks)} total"]
    for status in ("running", "pending", "done", "error"):
        count = counts.get(status, 0)
        if count:
            parts.append(f"{count} {status}")
    return " | ".join(parts)


def _describe_outcome(snapshot: SessionSnapshot) -> str:
    if snapshot.plan.outcome_summary:
        return snapshot.plan.outcome_summary
    if snapshot.meta.status == "completed":
        return "Session completed"
    if snapshot.meta.status == "aborted":
        return "Session aborted"
    if snapshot.meta.status == "failed":
        return "Session failed"
    return "Session active"


@click.group()
@click.version_option(version=None, package_name="openmax", prog_name="openmax")
def main() -> None:
    """openMax — Multi AI Agent orchestration hub."""


@main.command()
@click.argument("task")
@click.option("--cwd", default=None, help="Working directory for agents")
@click.option("--model", default=None, help="Model for the lead agent")
@click.option("--max-turns", default=50, type=click.IntRange(min=1), help="Max agent loop turns")
@click.option("--keep-panes", is_flag=True, default=False, help="Don't close panes on exit")
@click.option("--session-id", default=None, help="Persistent lead-agent session identifier")
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help="Resume a persistent lead-agent session",
)
@click.option(
    "--agents",
    default=None,
    help="Comma-separated list of allowed agent names (built-in or configured)",
)
def run(
    task: str,
    cwd: str | None,
    model: str | None,
    max_turns: int,
    keep_panes: bool,
    session_id: str | None,
    resume: bool,
    agents: str | None,
) -> None:
    """Decompose TASK and dispatch sub-agents in Kaku panes."""
    cwd = _resolve_cwd(cwd)

    if resume and not session_id:
        raise click.UsageError("--resume requires --session-id")

    try:
        agent_registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    available_agents = set(agent_registry.names())
    allowed_agents = _parse_allowed_agents(agents, available_agents)

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

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _cleanup_and_exit(signum, _frame):
        console.print("\n[yellow]Interrupted — cleaning up panes...[/yellow]")
        _do_cleanup()
        console.print("[green]All managed panes closed.[/green]")
        sys.exit(130 if signum == signal.SIGINT else 143)

    signal.signal(signal.SIGINT, _cleanup_and_exit)
    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    try:
        try:
            plan = run_lead_agent(
                task=task,
                pane_mgr=pane_mgr,
                cwd=cwd,
                model=model,
                max_turns=max_turns,
                session_id=session_id,
                resume=resume,
                allowed_agents=allowed_agents,
                agent_registry=agent_registry,
            )
        except LeadAgentStartupError as exc:
            raise SystemExit(1) from exc
        else:
            # Session complete — show final summary before cleanup
            summary = pane_mgr.summary()
            console.print(
                f"\n[bold green]Done.[/bold green] "
                f"{len(plan.subtasks)} sub-tasks | "
                f"{summary['total_windows']} windows | "
                f"{summary['done']} done"
            )
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
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
            f"  Pane {p.pane_id}: {p.title or '(untitled)'} [dim]({p.cols}x{p.rows})[/dim]{active}"
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


@main.command()
@click.option("--cwd", default=None, help="Workspace to inspect memory for")
@click.option(
    "--limit",
    default=10,
    type=click.IntRange(min=1),
    help="Maximum number of memory entries to show",
)
def memories(cwd: str | None, limit: int) -> None:
    """Show learned workspace memory that future runs can reuse."""
    cwd = _resolve_cwd(cwd)

    store = MemoryStore()
    lines = store.render_workspace_memories(cwd, limit=limit)
    if not lines:
        console.print(f"[yellow]No memory stored yet for {cwd}.[/yellow]")
        return

    console.print(f"[bold]Memory for {cwd}[/bold]")
    for line in lines:
        console.print(line)


@main.command("recommend-agents")
@click.argument("task")
@click.option("--cwd", default=None, help="Workspace to inspect memory for")
@click.option(
    "--limit",
    default=4,
    type=click.IntRange(min=1),
    help="Maximum number of agent recommendations",
)
def recommend_agents(task: str, cwd: str | None, limit: int) -> None:
    """Show ranked agent recommendations for a task in this workspace."""
    cwd = _resolve_cwd(cwd)

    store = MemoryStore()
    recommendations = store.derive_agent_rankings(cwd=cwd, task=task, limit=limit)
    if not recommendations:
        console.print("[yellow]No agent recommendations available yet.[/yellow]")
        return

    console.print(f"[bold]Agent recommendations for {task}[/bold]")
    for item in recommendations:
        console.print(f"- {item.agent_type}: {item.score}")
        for reason in item.reasons:
            console.print(f"  {reason}")


@main.command("list-agents")
@click.option("--cwd", default=None, help="Working directory used for workspace agent config")
def list_agents(cwd: str | None) -> None:
    """List built-in and configured agents."""
    cwd = _resolve_cwd(cwd)

    try:
        registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    console.print(f"[bold]Available agents for {cwd}[/bold]")
    for definition in registry.definitions():
        source = "built-in" if definition.built_in else definition.source
        console.print(f"- {definition.name} [dim]({source})[/dim]")


@main.command("runs")
@click.option(
    "--status",
    default=None,
    type=click.Choice(["active", "completed", "failed", "aborted"], case_sensitive=False),
    help="Filter sessions by persisted status",
)
@click.option(
    "--limit",
    default=10,
    type=click.IntRange(min=1),
    help="Maximum number of recent sessions to show",
)
def runs(status: str | None, limit: int) -> None:
    """List recent persisted sessions."""
    store = SessionStore()
    sessions = store.list_sessions(status=status.lower() if status else None, limit=limit)
    if not sessions:
        console.print("[yellow]No sessions found.[/yellow]")
        return

    console.print("[bold]Recent sessions[/bold]")
    for meta in sessions:
        latest_phase = meta.latest_phase or "unknown"
        completion = None
        try:
            snapshot = store.load_snapshot(meta.session_id)
            latest_phase = snapshot.plan.latest_phase or latest_phase
            completion = snapshot.plan.completion_pct
        except RuntimeError:
            pass
        console.print(
            " | ".join(
                [
                    meta.session_id,
                    meta.status,
                    f"phase={latest_phase}",
                    f"completion={_format_completion(completion)}",
                    f"updated={_format_timestamp(meta.updated_at)}",
                    meta.task,
                ]
            )
        )


@main.command()
@click.argument("session_id")
def inspect(session_id: str) -> None:
    """Inspect a persisted session."""
    store = SessionStore()
    try:
        snapshot = store.load_snapshot(session_id)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    meta = snapshot.meta
    plan = snapshot.plan
    console.print(f"[bold]Session:[/bold] {meta.session_id}")
    console.print(f"[bold]Task:[/bold] {meta.task}")
    console.print(f"[bold]Workspace:[/bold] {meta.cwd}")
    console.print(
        " | ".join(
            [
                f"status={meta.status}",
                f"created={_format_timestamp(meta.created_at)}",
                f"updated={_format_timestamp(meta.updated_at)}",
            ]
        )
    )
    console.print(
        " | ".join(
            [
                f"latest_phase={plan.latest_phase or 'unknown'}",
                f"completion={_format_completion(plan.completion_pct)}",
                f"subtasks={_render_subtask_counts(snapshot)}",
            ]
        ),
        soft_wrap=True,
    )
    console.print("[bold]Outcome[/bold]")
    console.print(f"status={meta.status}", soft_wrap=True, markup=False)
    console.print(f"summary={_describe_outcome(snapshot)}", soft_wrap=True, markup=False)
    if plan.report_notes:
        console.print(f"[bold]Report:[/bold] {plan.report_notes}", soft_wrap=True)

    console.print("[bold]Recent activity[/bold]")
    if not plan.recent_activity:
        console.print("- none")
    else:
        for item in plan.recent_activity[-8:]:
            console.print(f"- {item}", soft_wrap=True, markup=False)

    anchors = plan.anchors[-_ANCHOR_PREVIEW_LIMIT:]
    console.print("[bold]Anchors[/bold]")
    if not anchors:
        console.print("- none")
    else:
        for anchor in anchors:
            summary = anchor.summary or "no summary"
            console.print(
                f"- {anchor.phase} | {_format_timestamp(anchor.timestamp)} | {summary}"
            )

    console.print("[bold]Subtasks[/bold]")
    if not plan.subtasks:
        console.print("- none")
        return

    for task in plan.subtasks:
        parts = [task.name, task.status, task.agent_type]
        if task.pane_id is not None:
            parts.append(f"pane={task.pane_id}")
        console.print(f"- {' | '.join(parts)}")
