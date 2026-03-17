"""CLI entry point for openMax."""

from __future__ import annotations

import atexit
import os
import signal
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import click
from rich.table import Table

from openmax.agent_registry import AgentConfigError, load_agent_registry
from openmax.auth import has_claude_auth, run_claude_setup_token
from openmax.config import fetch_anthropic_models, get_model, set_model
from openmax.doctor import render_results, run_checks
from openmax.lead_agent import LeadAgentStartupError, run_lead_agent
from openmax.memory import MemoryStore
from openmax.output import clear_screen, console
from openmax.pane_backend import resolve_pane_backend_name
from openmax.pane_manager import PaneManager
from openmax.provider_usage import ProviderStatus, probe_all
from openmax.session_runtime import SessionSnapshot, SessionStore
from openmax.terminal import ensure_kaku, ensure_tmux, is_kaku_available, is_tmux_available
from openmax.usage import UsageStore

_ANCHOR_PREVIEW_LIMIT = 5

_STATUS_STYLES: dict[str, str] = {
    "completed": "green",
    "active": "yellow",
    "failed": "red",
    "aborted": "dim",
}

_SUBTASK_STATUS_STYLES: dict[str, str] = {
    "done": "green",
    "running": "yellow",
    "error": "red",
    "pending": "dim",
}


def _make_table(**overrides: Any) -> Table:
    defaults = dict(
        show_header=True,
        header_style="bold dim",
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
    )
    defaults.update(overrides)
    return Table(**defaults)


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


def _format_timestamp(value: str, short: bool = False) -> str:
    try:
        dt = datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return value
    if short:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_completion(value: int | None) -> str:
    return f"{value}%" if value is not None else "n/a"


def _detect_resumable_session(task: str, cwd: str) -> tuple[str | None, bool]:
    """Return (session_id, should_resume) if an unfinished session is found."""
    try:
        from openmax.session_runtime import SessionStore as _SS
        from openmax.session_runtime import task_hash as _th

        existing = _SS().find_active_session(_th(task, cwd))
        if not existing or existing.status in ("completed", "aborted", "failed"):
            return None, False
        ago_str = _format_session_age(existing.updated_at)
        pct = getattr(existing, "completion_pct", None)
        pct_str = f" ({pct}% complete)" if pct is not None else ""
        console.print(
            f"[yellow]Found unfinished session:[/yellow] {existing.session_id}{pct_str}, {ago_str}"
        )
        if click.confirm("Resume it?", default=True):
            return existing.session_id, True
        return None, False
    except Exception:
        return None, False


def _format_session_age(updated_at: str) -> str:
    try:
        ago_dt = datetime.fromisoformat(updated_at)
        delta = datetime.now(timezone.utc) - ago_dt
        mins = int(delta.total_seconds() / 60)
        return f"{mins}m ago" if mins < 120 else f"{mins // 60}h ago"
    except Exception:
        return "recently"


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
@click.option(
    "--pane-backend",
    "pane_backend_name",
    type=click.Choice(["kaku", "tmux", "headless", "auto"], case_sensitive=False),
    default=None,
    help="Pane backend to use (defaults to auto-detect: kaku > tmux)",
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
    pane_backend_name: str | None,
) -> None:
    """Decompose TASK and dispatch sub-agents in terminal panes."""
    cwd = _resolve_cwd(cwd)
    pane_backend_name = resolve_pane_backend_name(pane_backend_name)

    if resume and not session_id:
        raise click.UsageError("--resume requires --session-id")

    if not session_id and not resume:
        found_id, should_resume = _detect_resumable_session(task, cwd)
        if should_resume and found_id:
            session_id = found_id
            resume = True

    try:
        agent_registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    available_agents = set(agent_registry.names())
    allowed_agents = _parse_allowed_agents(agents, available_agents)

    if pane_backend_name == "kaku" and not ensure_kaku():
        raise SystemExit(1)
    if pane_backend_name == "tmux" and not ensure_tmux():
        raise SystemExit(1)

    clear_screen()
    pane_mgr = PaneManager(backend_name=pane_backend_name)

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
        console.print("\n[dim]Interrupted — cleaning up...[/dim]")
        _do_cleanup()
        sys.exit(130 if signum == signal.SIGINT else 143)

    signal.signal(signal.SIGINT, _cleanup_and_exit)
    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    try:
        effective_model = model or get_model()
        try:
            plan = run_lead_agent(
                task=task,
                pane_mgr=pane_mgr,
                cwd=cwd,
                model=effective_model,
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
                f"\n[bold]Done.[/bold] {len(plan.subtasks)} sub-tasks, {summary['done']} done"
            )
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if not keep_panes and not _cleaned_up:
            console.print("[dim]Closing panes...[/dim]")
            _do_cleanup()
        elif keep_panes:
            console.print("[dim]Keeping panes open (--keep-panes).[/dim]")


@main.command()
def panes() -> None:
    """List all terminal panes (kaku or tmux)."""
    if not is_kaku_available() and not is_tmux_available():
        console.print("[red]No pane backend available.[/red]\nRun inside Kaku (macOS) or tmux.")
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


@main.command("recommendation-eval")
@click.option("--cwd", default=None, help="Workspace to inspect memory for")
def recommendation_eval(cwd: str | None) -> None:
    """Evaluate structured recommendation quality from workspace memory."""
    cwd = _resolve_cwd(cwd)

    store = MemoryStore()
    report = store.evaluate_recommendations_against_baseline(cwd=cwd)
    if report.strategy.total_runs == 0:
        console.print("[yellow]No structured run summaries available yet.[/yellow]")
        return

    console.print(f"[bold]Recommendation eval[/bold]  [dim]{cwd}[/dim]")

    tbl = _make_table()
    tbl.add_column("", style="bold")
    tbl.add_column("Runs", justify="right")
    tbl.add_column("Evaluated", justify="right")
    tbl.add_column("Covered", justify="right")
    tbl.add_column("Hits", justify="right")
    tbl.add_column("Coverage", justify="right")
    tbl.add_column("Hit rate", justify="right")
    tbl.add_column("Avg %", justify="right")
    tbl.add_column("Fail rate", justify="right")

    s = report.strategy
    b = report.baseline
    tbl.add_row(
        "Strategy",
        str(s.total_runs),
        str(s.evaluated_runs),
        str(s.covered_runs),
        str(s.hit_runs),
        f"{s.coverage:.0%}",
        f"{s.hit_rate:.0%}",
        f"{s.average_completion_pct:.1f}%",
        f"{s.average_failure_rate:.0%}",
    )
    tbl.add_row(
        f"Baseline ({b.label})",
        str(b.total_runs),
        str(b.evaluated_runs),
        str(b.covered_runs),
        str(b.hit_runs),
        f"{b.coverage:.0%}",
        f"{b.hit_rate:.0%}",
        f"{b.average_completion_pct:.1f}%",
        f"{b.average_failure_rate:.0%}",
    )
    tbl.add_row(
        "[dim]Delta[/dim]",
        "",
        "",
        "",
        "",
        f"{report.coverage_delta:+.0%}",
        f"{report.hit_rate_lift:+.0%}",
        f"{report.completion_pct_delta:+.1f}pts",
        f"{report.failure_rate_delta:+.0%}",
    )
    console.print(tbl)


@main.command("list-agents")
@click.option("--cwd", default=None, help="Working directory used for workspace agent config")
@click.option(
    "--verbose", "-v", is_flag=True, default=False, help="Show command template for each agent"
)
def list_agents(cwd: str | None, verbose: bool) -> None:
    """List built-in and configured agents."""
    cwd = _resolve_cwd(cwd)

    try:
        registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    console.print(f"[bold]Available agents for {cwd}[/bold]")
    for definition in registry.definitions():
        source = "built-in" if definition.built_in else definition.source
        line = f"- {definition.name} [dim]({source})[/dim]"
        if verbose:
            adapter = definition.adapter
            if hasattr(adapter, "_command_template"):
                cmd_preview = " ".join(adapter._command_template)[:60]
                line += f"\n    cmd: {cmd_preview}"
        console.print(line)


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

    tbl = _make_table(expand=True)
    tbl.add_column("Session", style="bold", no_wrap=True)
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Phase", style="dim", no_wrap=True)
    tbl.add_column("%", justify="right", no_wrap=True)
    tbl.add_column("Updated", style="dim", no_wrap=True)
    tbl.add_column("Task", max_width=36, no_wrap=True, overflow="ellipsis")

    for meta in sessions:
        latest_phase = meta.latest_phase or "-"
        completion = None
        try:
            snapshot = store.load_snapshot(meta.session_id)
            latest_phase = snapshot.plan.latest_phase or latest_phase
            completion = snapshot.plan.completion_pct
        except RuntimeError:
            pass

        status_style = _STATUS_STYLES.get(meta.status, "white")

        tbl.add_row(
            meta.session_id[:16],
            f"[{status_style}]{meta.status}[/{status_style}]",
            latest_phase,
            _format_completion(completion),
            _format_timestamp(meta.updated_at, short=True),
            meta.task[:50],
        )

    console.print(tbl)


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

    status_style = _STATUS_STYLES.get(meta.status, "white")

    # Header
    console.print(f"[bold]{meta.session_id}[/bold]  [{status_style}]{meta.status}[/{status_style}]")
    console.print(f"  [dim]task:[/dim]      {meta.task}")
    console.print(f"  [dim]workspace:[/dim] {meta.cwd}")
    console.print(
        f"  [dim]created:[/dim]   {_format_timestamp(meta.created_at)}  "
        f"[dim]updated:[/dim] {_format_timestamp(meta.updated_at)}"
    )
    console.print(
        f"  [dim]phase:[/dim]     {plan.latest_phase or 'unknown'}  "
        f"[dim]completion:[/dim] {_format_completion(plan.completion_pct)}  "
        f"[dim]subtasks:[/dim] {_render_subtask_counts(snapshot)}"
    )

    # Outcome
    console.print()
    console.print(f"  [dim]outcome:[/dim]   {_describe_outcome(snapshot)}")
    if plan.report_notes:
        console.print(f"  [dim]report:[/dim]    {plan.report_notes}")

    usage_rec = UsageStore().load(meta.session_id)
    if usage_rec:
        console.print(f"  [dim]usage:[/dim]     {usage_rec.summary_line()}")

    # Scorecard
    console.print()
    console.print(f"  [dim]scorecard:[/dim] {plan.scorecard.surface_summary}")
    if plan.scorecard.surface_details:
        console.print(f"             {plan.scorecard.surface_details}")

    if snapshot.load_warnings:
        console.print()
        console.print("[bold yellow]Diagnostics[/bold yellow]")
        for warning in snapshot.load_warnings:
            console.print(f"  - {warning}", soft_wrap=True, markup=False)

    # Activity
    if plan.recent_activity:
        console.print()
        console.print("[bold]Recent activity[/bold]")
        for item in plan.recent_activity[-8:]:
            console.print(f"  {item}", soft_wrap=True, markup=False)

    # Anchors
    anchors = plan.anchors[-_ANCHOR_PREVIEW_LIMIT:]
    if anchors:
        console.print()
        console.print("[bold]Anchors[/bold]")
        for anchor in anchors:
            summary = anchor.summary or "no summary"
            console.print(f"  {anchor.phase:12s} {_format_timestamp(anchor.timestamp)}  {summary}")

    # Subtasks table
    if plan.subtasks:
        console.print()
        tbl = _make_table(title="Subtasks", title_style="bold", expand=True)
        tbl.add_column("Name", style="bold")
        tbl.add_column("Status")
        tbl.add_column("Agent", style="dim")
        tbl.add_column("Pane", justify="right", style="dim")

        for task in plan.subtasks:
            st_style = _SUBTASK_STATUS_STYLES.get(task.status, "white")
            pane_str = str(task.pane_id) if task.pane_id is not None else "-"
            tbl.add_row(
                task.name,
                f"[{st_style}]{task.status}[/{st_style}]",
                task.agent_type,
                pane_str,
            )
        console.print(tbl)


@main.command()
@click.argument("session_id", required=False, default=None)
@click.option(
    "--limit",
    default=10,
    type=click.IntRange(min=1),
    help="Maximum number of recent sessions to show",
)
@click.option("--total", is_flag=True, default=False, help="Show aggregate totals")
def usage(session_id: str | None, limit: int, total: bool) -> None:
    """Show usage (cost, tokens, duration) for sessions.

    If SESSION_ID is provided, show usage for that session only.
    Otherwise list recent sessions with their usage.
    """
    store = UsageStore()

    if session_id:
        rec = store.load(session_id)
        if rec is None:
            console.print(f"[yellow]No usage data for session '{session_id}'.[/yellow]")
            raise SystemExit(1)
        console.print(f"[bold]Session:[/bold] {rec.session_id}")
        console.print(f"[bold]Cost:[/bold]     {rec.format_cost()}")
        console.print(f"[bold]Tokens:[/bold]   {rec.total_tokens:,} ({rec.format_tokens()})")
        console.print(f"[bold]Duration:[/bold] {rec.format_duration()}")
        console.print(f"[bold]API time:[/bold] {rec.duration_api_ms / 1000:.1f}s")
        console.print(f"[bold]Turns:[/bold]    {rec.num_turns}")
        console.print(f"[bold]Recorded:[/bold] {_format_timestamp(rec.recorded_at)}")
        return

    records = store.list_all(limit=limit)
    if not records:
        console.print("[yellow]No usage data recorded yet.[/yellow]")
        return

    tbl = _make_table(title="Session usage", title_style="bold", expand=True)
    tbl.add_column("Session", style="bold")
    tbl.add_column("Cost", justify="right")
    tbl.add_column("Tokens", justify="right")
    tbl.add_column("Duration", justify="right")
    tbl.add_column("Turns", justify="right")

    for rec in records:
        tbl.add_row(
            rec.session_id[:12],
            rec.format_cost(),
            f"{rec.total_tokens:,}",
            rec.format_duration(),
            str(rec.num_turns),
        )
    console.print(tbl)

    if total or len(records) > 1:
        agg = store.aggregate(records)
        console.print(f"\n[bold]Total ({len(records)} sessions):[/bold] {agg.summary_line()}")


@main.command()
@click.option("--daily", is_flag=True, default=False, help="Show daily activity breakdown")
@click.option(
    "--days",
    default=7,
    type=click.IntRange(min=1),
    help="Number of days to show in daily view",
)
def status(daily: bool, days: int) -> None:
    """Show subscription usage for installed coding agents."""
    providers = probe_all()
    installed = [p for p in providers if p.installed]

    if not installed:
        console.print("[yellow]No coding agents found on this system.[/yellow]")
        raise SystemExit(1)

    for provider in installed:
        _render_provider_card(provider, daily=daily, days=days)

    if len(installed) > 1:
        _render_total_summary(installed)


def _render_total_summary(providers: list[ProviderStatus]) -> None:
    from rich.panel import Panel

    total_sessions = sum(p.total_sessions for p in providers)
    total_messages = sum(p.total_messages for p in providers)
    total_tokens = sum(p.total_tokens for p in providers)

    parts: list[str] = []
    parts.append(
        f"  [bold]{len(providers)}[/bold] agents  "
        f"[bold]{total_sessions:,}[/bold] sessions  "
        f"[bold]{total_messages:,}[/bold] messages  "
        f"[bold]{_compact_num(total_tokens)}[/bold] tokens"
    )

    # Per-provider one-liner with quota highlight
    for p in providers:
        name = _provider_display_name(p.provider)
        q = p.quota
        if q and q.windows:
            # Show the tightest (most used) window
            tightest = max(q.windows, key=lambda w: w.used_pct)
            remaining = max(100.0 - tightest.used_pct, 0)
            if remaining <= 0:
                badge = "[bold red]LIMIT[/bold red]"
            elif remaining <= 20:
                badge = f"[red]{remaining:.0f}%[/red]"
            elif remaining <= 50:
                badge = f"[yellow]{remaining:.0f}%[/yellow]"
            else:
                badge = f"[green]{remaining:.0f}%[/green]"
            parts.append(
                f"  {name:14s}  {badge} left ({tightest.name})  "
                f"[dim]{_compact_num(p.total_tokens)} tokens[/dim]"
            )
        else:
            parts.append(f"  {name:14s}  [dim]{_compact_num(p.total_tokens)} tokens[/dim]")

    body = "\n".join(parts)
    panel = Panel(
        body,
        title="[bold white]Total[/bold white]",
        title_align="left",
        border_style="dim",
        padding=(0, 2),
    )
    console.print(panel)


def _render_provider_card(
    p: ProviderStatus,
    *,
    daily: bool = False,
    days: int = 7,
) -> None:
    from rich.panel import Panel

    parts: list[str] = []

    # ── Title bar ──────────────────────────────────────
    name = _provider_display_name(p.provider)
    title_parts = [f"[bold white]{name}[/bold white]"]
    if p.version:
        title_parts.append(f"[dim]{p.version}[/dim]")
    if p.plan:
        title_parts.append(f"[bold cyan]{p.plan}[/bold cyan]")
    if p.model:
        title_parts.append(f"[green]{_short_model(p.model)}[/green]")
    title = "  ".join(title_parts)

    # ── Quota (hero section — the most important info) ─
    q = p.quota
    if q and q.windows:
        for win in q.windows:
            remaining = max(100.0 - win.used_pct, 0)
            bar = _quota_bar(win.used_pct)
            reset_str = ""
            if win.resets_at:
                reset_str = f"  [dim]resets {_format_reset(win.resets_at)}[/dim]"
            elif win.reset_seconds:
                reset_str = f"  [dim]resets in {_format_seconds(win.reset_seconds)}[/dim]"
            parts.append(
                f"  {win.name:16s}  {bar}  [bold]{remaining:.0f}% remaining[/bold]{reset_str}"
            )
        # Extra usage / overages
        if q.extra_usage_enabled:
            if q.extra_usage_limit > 0:
                parts.append(
                    f"  {'extra usage':16s}  ${q.extra_usage_used:.2f} / ${q.extra_usage_limit:.0f}"
                )
            else:
                parts.append(f"  {'extra usage':16s}  ${q.extra_usage_used:.2f} used")
        if q.error and q.error != "rate limit reached":
            parts.append(f"  [dim]quota: {q.error}[/dim]")
        if q.error == "rate limit reached":
            parts.append("  [bold red]RATE LIMITED[/bold red]")
        parts.append("")
    elif q and q.error:
        parts.append(f"  [dim]quota: {q.error}[/dim]")
        parts.append("")

    # ── 5-hour local window ────────────────────────────
    w = p.window_usage
    if w and w.messages > 0:
        model_parts = []
        for model, count in sorted(w.models.items(), key=lambda x: x[1], reverse=True):
            model_parts.append(f"{_short_model(model)} x{count}")
        parts.append(
            f"  [bold]Local 5h[/bold]  "
            f"[bold]{_compact_num(w.total_tokens)}[/bold] tokens  "
            f"[dim]in={_compact_num(w.input_tokens)} "
            f"out={_compact_num(w.output_tokens)} "
            f"cache_r={_compact_num(w.cache_read_tokens)} "
            f"cache_w={_compact_num(w.cache_creation_tokens)}[/dim]"
        )
        parts.append(f"  [dim]{' | '.join(model_parts)}[/dim]")
        parts.append("")

    # ── Lifetime stats ─────────────────────────────────
    parts.append(
        f"  [bold]All time[/bold]  "
        f"{p.total_sessions:,} sessions  "
        f"{p.total_messages:,} messages  "
        f"{_compact_num(p.total_tokens)} tokens"
    )

    # ── Model breakdown ────────────────────────────────
    if p.model_usage:
        for mu in sorted(p.model_usage, key=lambda m: m.total_tokens, reverse=True):
            pct = mu.total_tokens / p.total_tokens * 100 if p.total_tokens else 0
            bar = _mini_bar(pct)
            parts.append(
                f"  {bar} {_short_model(mu.model):12s}  "
                f"[bold]{_compact_num(mu.total_tokens):>6s}[/bold]  "
                f"[dim]in={_compact_num(mu.input_tokens)} "
                f"out={_compact_num(mu.output_tokens)}[/dim]"
            )

    # ── Daily sparkline ────────────────────────────────
    if p.daily_activity:
        recent = p.daily_activity[-days:]
        day_tokens = [sum(d.tokens_by_model.values()) for d in recent]
        spark = _sparkline(day_tokens)
        dates_range = f"{recent[0].date} ~ {recent[-1].date}"
        parts.append("")
        parts.append(f"  [bold]Daily[/bold]  [dim]{dates_range}[/dim]")
        parts.append(f"  {spark}")

        if daily:
            max_day = max(day_tokens) if day_tokens else 1
            for day in recent:
                total_day_tokens = sum(day.tokens_by_model.values())
                day_bar = _mini_bar(total_day_tokens / max_day * 100 if max_day else 0)
                detail_parts: list[str] = []
                if day.sessions:
                    detail_parts.append(f"{day.sessions} sess")
                if day.messages:
                    detail_parts.append(f"{day.messages:,} msg")
                if day.tool_calls:
                    detail_parts.append(f"{day.tool_calls:,} tools")
                detail = "  ".join(detail_parts)
                parts.append(
                    f"  {day.date}  {day_bar}  "
                    f"[bold]{_compact_num(total_day_tokens):>6s}[/bold]  "
                    f"[dim]{detail}[/dim]"
                )

    if p.error:
        parts.append(f"\n  [red]! {p.error}[/red]")

    body = "\n".join(parts)
    panel = Panel(
        body,
        title=title,
        title_align="left",
        border_style="dim cyan",
        padding=(1, 2),
    )
    console.print(panel)


def _quota_bar(used_pct: float) -> str:
    """20-char quota bar: green when plenty left, red when near limit."""
    filled = min(int(used_pct / 5), 20)
    empty = 20 - filled
    if used_pct >= 90:
        color = "bold red"
    elif used_pct >= 70:
        color = "yellow"
    else:
        color = "green"
    return f"[{color}]{'█' * filled}[/][dim]{'░' * empty}[/dim]"


def _format_reset(iso_str: str) -> str:
    """Format a reset timestamp as relative time."""
    try:
        reset_dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        delta = reset_dt - now
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "now"
        return _format_seconds(secs)
    except (ValueError, TypeError):
        return iso_str


def _format_seconds(secs: int) -> str:
    """Format seconds as human-readable duration."""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    if hours < 24:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d {remaining_hours}h" if remaining_hours else f"{days}d"


def _mini_bar(pct: float) -> str:
    """Tiny 8-char proportional bar."""
    filled = min(int(pct / 12.5), 8)
    return "[cyan]" + "█" * filled + "[/cyan]" + "[dim]░[/dim]" * (8 - filled)


def _sparkline(values: list[int]) -> str:
    """Unicode sparkline from a list of values."""
    if not values:
        return ""
    blocks = "▁▁▂▃▄▅▆▇█"
    max_val = max(values) if max(values) > 0 else 1
    chars = []
    for v in values:
        idx = min(int(v / max_val * 8), 8) if v > 0 else 0
        chars.append(blocks[idx])
    return "[cyan]" + "".join(chars) + "[/cyan]"


def _compact_num(n: int) -> str:
    """Format large numbers compactly: 1,234 -> 1.2K, 1,234,567 -> 1.2M."""
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n / 1_000:.1f}K"
    if n < 1_000_000:
        return f"{n / 1_000:.0f}K"
    if n < 10_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.0f}M"
    return f"{n / 1_000_000_000:.1f}B"


def _short_model(name: str) -> str:
    """Shorten model names for display."""
    replacements = {
        "claude-opus-4-6": "opus-4.6",
        "claude-sonnet-4-6": "sonnet-4.6",
        "claude-opus-4-5-20251101": "opus-4.5",
        "claude-sonnet-4-5-20251001": "sonnet-4.5",
        "claude-haiku-4-5-20251001": "haiku-4.5",
    }
    return replacements.get(name, name)


def _provider_display_name(provider: str) -> str:
    names = {
        "claude-code": "Claude Code",
        "codex": "Codex CLI",
    }
    return names.get(provider, provider.replace("-", " ").title())


@main.command()
def doctor() -> None:
    """Check the environment — Python, terminal backends, agent CLIs, and auth."""
    results = run_checks()
    lines, issue_count = render_results(results)
    for line in lines:
        console.print(line)
    raise SystemExit(0 if issue_count == 0 else 1)


@main.command("validate-config")
@click.option("--cwd", default=None, help="Workspace to validate agent config for")
def validate_config(cwd: str | None) -> None:
    """Validate built-in and custom agent configuration."""
    cwd = _resolve_cwd(cwd)
    console.print(f"[bold]Validating agent config for {cwd}[/bold]")

    from pathlib import Path

    from openmax.agent_registry import (
        _candidate_config_paths,
        _merge_config_file,
        built_in_agent_registry,
    )

    registry = built_in_agent_registry()
    console.print("[dim]Built-in agents:[/dim] " + ", ".join(registry.names()))

    found_errors = False
    for path, required in _candidate_config_paths(cwd):
        p = Path(path)
        if not p.exists():
            continue
        console.print(f"\n[bold]Config file:[/bold] {p}")
        try:
            _merge_config_file(registry, p)
            console.print("  [green]✅ valid[/green]")
        except AgentConfigError as exc:
            console.print(f"  [red]❌ {exc}[/red]")
            found_errors = True

    if not found_errors:
        console.print("\n[green]All configs valid.[/green]")
    else:
        raise SystemExit(1)


@main.command()
@click.option("--status", is_flag=True, default=False, help="Show current auth status")
def setup(status: bool) -> None:
    """Set up Claude authentication for openMax.

    Runs `claude setup-token` to configure a long-lived token
    that avoids OAuth expiration issues.
    """
    if status:
        ok, detail = has_claude_auth()
        if ok:
            console.print(f"[green]Auth OK:[/green] {detail}")
        else:
            console.print("[yellow]No auth configured.[/yellow]")
            console.print("Run [bold]openmax setup[/bold] to configure.")
        return

    # Skip if already authenticated
    already_ok, detail = has_claude_auth()
    if already_ok:
        console.print(f"[green]Already authenticated:[/green] {detail}")
        console.print("No setup needed.")
        return

    import shutil

    if not shutil.which("claude"):
        console.print("[red]claude CLI not found.[/red]")
        console.print("Install it first: https://docs.anthropic.com/en/docs/claude-code")
        raise SystemExit(1)

    console.print("[bold]openMax Setup[/bold]\n")
    console.print(
        "This will run [bold]claude setup-token[/bold] to configure a long-lived token.\n"
    )

    ok = run_claude_setup_token()
    if ok:
        console.print("\n[green]Setup complete.[/green]")
    else:
        console.print("\n[red]Setup failed.[/red]")
        raise SystemExit(1)


@main.command()
def models() -> None:
    """Select the lead agent model and save it to config."""
    current = get_model()
    if current:
        console.print(f"[dim]Current model:[/dim] [bold]{current}[/bold]\n")

    console.print("[dim]Fetching available models...[/dim]")
    model_ids = fetch_anthropic_models()

    if not model_ids:
        console.print("[yellow]Could not fetch models — is ANTHROPIC_API_KEY set?[/yellow]")
        console.print("\nEnter a model ID manually:")
        model_ids = []

    if model_ids:
        console.print()
        for i, mid in enumerate(model_ids, 1):
            marker = " [green]✓[/green]" if mid == current else ""
            console.print(f"  [bold]{i}.[/bold] {mid}{marker}")
        console.print()
        raw = input("Select model (number or paste ID): ").strip()
    else:
        raw = input("Model ID: ").strip()

    if not raw:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    if raw.isdigit() and model_ids:
        idx = int(raw) - 1
        if not (0 <= idx < len(model_ids)):
            console.print("[red]Invalid selection.[/red]")
            raise SystemExit(1)
        chosen = model_ids[idx]
    else:
        chosen = raw

    set_model(chosen)
    console.print(f"\n[green]Model set:[/green] [bold]{chosen}[/bold]")
    console.print("[dim]Used by future `openmax run` calls (override with --model).[/dim]")
