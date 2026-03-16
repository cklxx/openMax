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
from openmax.auth import has_claude_auth, run_claude_setup_token
from openmax.doctor import render_results, run_checks
from openmax.kaku import ensure_kaku, is_kaku_available
from openmax.lead_agent import LeadAgentStartupError, run_lead_agent
from openmax.memory import MemoryStore
from openmax.pane_backend import resolve_pane_backend_name
from openmax.pane_manager import PaneManager
from openmax.provider_usage import ProviderStatus, probe_all
from openmax.session_runtime import SessionSnapshot, SessionStore
from openmax.usage import UsageStore

console = Console()
_ANCHOR_PREVIEW_LIMIT = 5


def _interactive_loop(pane_mgr: PaneManager, plan: object) -> None:
    """Post-run interactive mode for inspecting/sending to panes."""
    console.print(
        "\n[bold cyan]Interactive mode[/bold cyan] — "
        "commands: inspect <pane_id>, send <pane_id> <text>, summary, quit"
    )
    while True:
        try:
            line = input("openmax> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            break
        if not line:
            continue
        parts = line.split(None, 2)
        cmd = parts[0].lower()

        if cmd in ("quit", "q"):
            break
        elif cmd == "summary":
            s = pane_mgr.summary()
            console.print(f"Windows: {s['total_windows']} | Done: {s['done']}")
            if hasattr(plan, "subtasks"):
                for st in plan.subtasks:
                    p_id = getattr(st, "pane_id", None)
                    console.print(f"  {st.name} | {st.status.value} | pane={p_id}")
        elif cmd == "inspect":
            if len(parts) < 2:
                console.print("[red]Usage: inspect <pane_id>[/red]")
                continue
            try:
                pane_id = int(parts[1])
                text = pane_mgr.get_text(pane_id)
                console.print(text[-2000:] if len(text) > 2000 else text)
            except (ValueError, RuntimeError) as exc:
                console.print(f"[red]{exc}[/red]")
        elif cmd == "send":
            if len(parts) < 3:
                console.print("[red]Usage: send <pane_id> <text>[/red]")
                continue
            try:
                pane_id = int(parts[1])
                pane_mgr.send_text(pane_id, parts[2])
                console.print(f"[green]Sent to pane {pane_id}[/green]")
            except (ValueError, RuntimeError) as exc:
                console.print(f"[red]{exc}[/red]")
        else:
            console.print("[yellow]Unknown command.[/yellow] Try: inspect, send, summary, quit")


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
@click.option(
    "--pane-backend",
    "pane_backend_name",
    type=click.Choice(["kaku", "headless"], case_sensitive=False),
    default=None,
    help="Pane backend to use (defaults to OPENMAX_PANE_BACKEND or kaku)",
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
    """Decompose TASK and dispatch sub-agents in Kaku panes."""
    cwd = _resolve_cwd(cwd)
    pane_backend_name = resolve_pane_backend_name(pane_backend_name)

    if resume and not session_id:
        raise click.UsageError("--resume requires --session-id")

    # Auto-resume: detect unfinished session for same task+cwd (when no explicit session-id)
    if not session_id and not resume:
        try:
            from openmax.session_runtime import SessionStore as _SS
            from openmax.session_runtime import task_hash as _th

            _store = _SS()
            _th_val = _th(task, cwd)
            _existing = _store.find_active_session(_th_val)
            if _existing and _existing.status not in ("completed", "aborted", "failed"):
                import datetime as _dt

                try:
                    _ago_dt = _dt.datetime.fromisoformat(_existing.updated_at)
                    _delta = _dt.datetime.now(_dt.timezone.utc) - _ago_dt
                    _mins = int(_delta.total_seconds() / 60)
                    _ago_str = f"{_mins}m ago" if _mins < 120 else f"{_mins // 60}h ago"
                except Exception:
                    _ago_str = "recently"
                _pct = getattr(_existing, "completion_pct", None)
                _pct_str = f" ({_pct}% complete)" if _pct is not None else ""
                console.print(
                    f"[yellow]Found unfinished session:[/yellow] {_existing.session_id}"
                    f"{_pct_str}, {_ago_str}"
                )
                if click.confirm("Resume it?", default=True):
                    session_id = _existing.session_id
                    resume = True
        except Exception:
            pass  # best-effort — don't block normal run

    try:
        agent_registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    available_agents = set(agent_registry.names())
    allowed_agents = _parse_allowed_agents(agents, available_agents)

    if pane_backend_name == "kaku" and not ensure_kaku():
        raise SystemExit(1)

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
            if not keep_panes and sys.stdout.isatty():
                _interactive_loop(pane_mgr, plan)
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

    console.print(f"[bold]Offline recommendation eval for {cwd}[/bold]")
    console.print(
        "Strategy: "
        + " | ".join(
            [
                f"runs={report.strategy.total_runs}",
                f"evaluated={report.strategy.evaluated_runs}",
                f"covered={report.strategy.covered_runs}",
                f"hits={report.strategy.hit_runs}",
            ]
        )
    )
    console.print(
        "  "
        + " | ".join(
            [
                f"coverage={report.strategy.coverage:.0%}",
                f"hit_rate={report.strategy.hit_rate:.0%}",
                f"avg_completion={report.strategy.average_completion_pct:.1f}%",
                f"avg_failure_rate={report.strategy.average_failure_rate:.0%}",
            ]
        )
    )
    console.print(
        "Baseline: "
        + " | ".join(
            [
                f"policy={report.baseline.label}",
                f"runs={report.baseline.total_runs}",
                f"evaluated={report.baseline.evaluated_runs}",
                f"covered={report.baseline.covered_runs}",
                f"hits={report.baseline.hit_runs}",
            ]
        )
    )
    console.print(
        "  "
        + " | ".join(
            [
                f"coverage={report.baseline.coverage:.0%}",
                f"hit_rate={report.baseline.hit_rate:.0%}",
                f"avg_completion={report.baseline.average_completion_pct:.1f}%",
                f"avg_failure_rate={report.baseline.average_failure_rate:.0%}",
            ]
        )
    )
    console.print(
        "Delta: "
        + " | ".join(
            [
                f"coverage_delta={report.coverage_delta:+.0%}",
                f"hit_rate_lift={report.hit_rate_lift:+.0%}",
                f"completion_delta={report.completion_pct_delta:+.1f}pts",
                f"failure_rate_delta={report.failure_rate_delta:+.0%}",
            ]
        )
    )


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

    console.print("[bold]Recent sessions[/bold]")
    for meta in sessions:
        latest_phase = meta.latest_phase or "unknown"
        completion = None
        scorecard_surface: str | None = None
        try:
            snapshot = store.load_snapshot(meta.session_id)
            latest_phase = snapshot.plan.latest_phase or latest_phase
            completion = snapshot.plan.completion_pct
            scorecard_surface = (
                f"scorecard={snapshot.plan.scorecard.surface_summary} | "
                f"{snapshot.plan.scorecard.surface_details}"
            )
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
        if scorecard_surface is not None:
            console.print(scorecard_surface, soft_wrap=True, markup=False)
        if snapshot.load_warnings:
            console.print(
                "warnings=" + " | ".join(snapshot.load_warnings),
                soft_wrap=True,
                markup=False,
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

    usage_rec = UsageStore().load(meta.session_id)
    if usage_rec:
        console.print(f"[bold]Usage:[/bold] {usage_rec.summary_line()}")

    console.print("[bold]Scorecard[/bold]")
    console.print(
        plan.scorecard.surface_summary,
        soft_wrap=True,
        markup=False,
    )
    console.print(
        plan.scorecard.surface_details,
        soft_wrap=True,
        markup=False,
    )

    if snapshot.load_warnings:
        console.print("[bold]Diagnostics[/bold]")
        for warning in snapshot.load_warnings:
            console.print(f"- {warning}", soft_wrap=True, markup=False)

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
            console.print(f"- {anchor.phase} | {_format_timestamp(anchor.timestamp)} | {summary}")

    console.print("[bold]Subtasks[/bold]")
    if not plan.subtasks:
        console.print("- none")
        return

    for task in plan.subtasks:
        parts = [task.name, task.status, task.agent_type]
        if task.pane_id is not None:
            parts.append(f"pane={task.pane_id}")
        console.print(f"- {' | '.join(parts)}")


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

    console.print("[bold]Recent session usage[/bold]")
    for rec in records:
        console.print(
            f"  {rec.session_id} | "
            f"{rec.format_cost()} | "
            f"{rec.total_tokens:,} tokens | "
            f"{rec.format_duration()} | "
            f"{rec.num_turns} turns"
        )

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
        border_style="blue",
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
    """Check the environment — Python, Kaku, agent CLIs, and auth."""
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
