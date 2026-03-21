"""CLI entry point for openMax."""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from openmax._paths import utc_now_iso
from openmax.agent_registry import AgentConfigError, load_agent_registry
from openmax.auth import has_claude_auth, run_claude_setup_token
from openmax.config import fetch_anthropic_models, get_model, set_model
from openmax.doctor import render_results, run_checks
from openmax.lead_agent import LeadAgentStartupError, run_lead_agent
from openmax.lead_agent.types import TaskStatus
from openmax.loop_session import LoopIteration, LoopSessionStore, build_loop_context
from openmax.output import console
from openmax.pane_backend import resolve_pane_backend_name
from openmax.pane_manager import PaneManager
from openmax.provider_usage import ProviderStatus, probe_all
from openmax.session_runtime import SessionSnapshot, SessionStore
from openmax.terminal import (
    ensure_ghostty,
    ensure_kaku,
    ensure_tmux,
    is_ghostty_available,
    is_kaku_available,
    is_tmux_available,
)
from openmax.theme import get_theme
from openmax.usage import UsageStore

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

_ANCHOR_PREVIEW_LIMIT = 5


def _status_styles() -> dict[str, str]:
    t = get_theme()
    return {
        "completed": t.session_completed,
        "active": t.session_active,
        "failed": t.session_failed,
        "aborted": t.session_aborted,
    }


def _subtask_status_styles() -> dict[str, str]:
    t = get_theme()
    return {
        "done": t.subtask_done,
        "running": t.subtask_running,
        "error": t.subtask_error,
        "pending": t.subtask_pending,
    }


_OPENMAX_MCP_SERVER_NAME = "openmax"
_OPENMAX_MCP_SERVER_CONFIG = {
    "type": "stdio",
    "command": "openmax-mcp",
    "args": [],
}


def _make_table(**overrides: Any) -> Table:
    defaults = dict(
        show_header=True,
        header_style=get_theme().header_default,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
    )
    defaults.update(overrides)
    return Table(**defaults)


def _resolve_cwd(cwd: str | None) -> str:
    return os.path.realpath(cwd or os.getcwd())


def _claude_config_path() -> Path:
    return Path.home() / ".claude.json"


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _load_claude_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid Claude config at {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise click.ClickException(
            f"Invalid Claude config at {config_path}: top-level JSON must be an object"
        )
    return data


def _load_codex_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(f"Invalid Codex config at {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise click.ClickException(
            f"Invalid Codex config at {config_path}: top-level TOML must be a table"
        )
    return data


def _claude_openmax_mcp_registered(config_path: Path | None = None) -> bool:
    config_path = config_path or _claude_config_path()
    config = _load_claude_config(config_path)

    servers = config.get("mcpServers")
    if servers is None:
        return False
    if not isinstance(servers, dict):
        raise click.ClickException(
            f"Invalid Claude config at {config_path}: `mcpServers` must be an object"
        )
    return servers.get(_OPENMAX_MCP_SERVER_NAME) == _OPENMAX_MCP_SERVER_CONFIG


def _codex_openmax_mcp_registered(config_path: Path | None = None) -> bool:
    config_path = config_path or _codex_config_path()
    config = _load_codex_config(config_path)

    servers = config.get("mcp_servers")
    if servers is None:
        return False
    if not isinstance(servers, dict):
        raise click.ClickException(
            f"Invalid Codex config at {config_path}: `mcp_servers` must be a table"
        )

    server = servers.get(_OPENMAX_MCP_SERVER_NAME)
    if not isinstance(server, dict):
        return False

    args = server.get("args")
    return (
        server.get("command") == _OPENMAX_MCP_SERVER_CONFIG["command"]
        and server.get("url") in (None, "")
        and args in (None, [])
    )


def _register_openmax_mcp_server(config_path: Path | None = None) -> bool:
    config_path = config_path or _claude_config_path()
    config = _load_claude_config(config_path)

    servers = config.get("mcpServers")
    if servers is None:
        servers = {}
    elif not isinstance(servers, dict):
        raise click.ClickException(
            f"Invalid Claude config at {config_path}: `mcpServers` must be an object"
        )

    if servers.get(_OPENMAX_MCP_SERVER_NAME) == _OPENMAX_MCP_SERVER_CONFIG:
        return False

    config["mcpServers"] = {
        **servers,
        _OPENMAX_MCP_SERVER_NAME: dict(_OPENMAX_MCP_SERVER_CONFIG),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _register_openmax_codex_mcp_server(config_path: Path | None = None) -> bool | None:
    config_path = config_path or _codex_config_path()
    if _codex_openmax_mcp_registered(config_path):
        return False

    if not shutil.which("codex"):
        return None

    result = subprocess.run(
        [
            "codex",
            "mcp",
            "add",
            _OPENMAX_MCP_SERVER_NAME,
            "--",
            _OPENMAX_MCP_SERVER_CONFIG["command"],
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise click.ClickException(f"Failed to register Codex MCP server: {detail}")
    return True


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


def _inspect_elapsed(task) -> str:
    """Compute elapsed string from a SubtaskState's started_at/finished_at (epoch floats)."""
    start = getattr(task, "started_at", None)
    end = getattr(task, "finished_at", None)
    if not start:
        return "-"
    ref = end or time.time()
    secs = max(0, int(ref - start))
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


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
@click.option(
    "--max-turns", default=None, type=click.IntRange(min=1), help="Max turns (default: unlimited)"
)
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
    type=click.Choice(["kaku", "ghostty", "tmux", "headless", "auto"], case_sensitive=False),
    default=None,
    help="Pane backend to use (defaults to auto-detect: kaku > tmux)",
)
@click.option(
    "--no-confirm",
    is_flag=True,
    default=False,
    help="Skip interactive plan confirmation (for automation)",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show detailed subtask output")
@click.option("--no-tui", is_flag=True, default=False, help="Classic Rich status bar")
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
    no_confirm: bool,
    verbose: bool,
    no_tui: bool,
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
    if pane_backend_name == "ghostty" and not ensure_ghostty():
        raise SystemExit(1)
    if pane_backend_name == "tmux" and not ensure_tmux():
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
                plan_confirm=not no_confirm,
                verbose=verbose,
                tui=not no_tui,
            )
        except LeadAgentStartupError as exc:
            raise SystemExit(1) from exc
        else:
            # Session complete — show final summary before cleanup
            done_count = sum(1 for t in plan.subtasks if t.status == TaskStatus.DONE)
            console.print(f"\n[bold]Done.[/bold] {len(plan.subtasks)} sub-tasks, {done_count} done")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if not keep_panes and not _cleaned_up:
            console.print("[dim]Closing panes...[/dim]")
            _do_cleanup()
        elif keep_panes:
            console.print("[dim]Keeping panes open (--keep-panes).[/dim]")


@main.command()
@click.argument("goal")
@click.option("--cwd", default=None, help="Working directory for agents")
@click.option("--model", default=None, help="Model for the lead agent")
@click.option("--max-turns", default=50, type=click.IntRange(min=1), help="Max turns per iteration")
@click.option(
    "--max-iterations",
    default=0,
    type=click.IntRange(min=0),
    help="Max iterations to run (0 = unlimited)",
)
@click.option(
    "--delay",
    default=5,
    type=click.IntRange(min=0),
    help="Seconds to pause between iterations",
)
@click.option(
    "--agents",
    default=None,
    help="Comma-separated list of allowed agent names",
)
@click.option(
    "--pane-backend",
    "pane_backend_name",
    type=click.Choice(["kaku", "ghostty", "tmux", "headless", "auto"], case_sensitive=False),
    default=None,
    help="Pane backend to use",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show detailed subtask output")
def loop(
    goal: str,
    cwd: str | None,
    model: str | None,
    max_turns: int,
    max_iterations: int,
    delay: int,
    agents: str | None,
    pane_backend_name: str | None,
    verbose: bool,
) -> None:
    """Run openmax in a continuous loop, pursuing GOAL across unlimited iterations.

    Memory accumulates between iterations — the lead agent naturally discovers
    new improvements each run based on what was done before.

    Press Ctrl+C to stop gracefully.
    """
    cwd = _resolve_cwd(cwd)
    pane_backend_name = resolve_pane_backend_name(pane_backend_name)

    try:
        agent_registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    available_agents = set(agent_registry.names())
    allowed_agents = _parse_allowed_agents(agents, available_agents)
    effective_model = model or get_model()

    _print_loop_header(goal, max_iterations)

    loop_store = LoopSessionStore()
    loop_session = loop_store.create(goal=goal, cwd=cwd)
    console.print(f"[dim]Loop ID: {loop_session.loop_id}[/dim]\n")

    iteration = 0
    try:
        while max_iterations == 0 or iteration < max_iterations:
            iteration += 1
            console.print(f"[bold]─── Iteration {iteration} {'─' * 40}[/bold]")
            loop_context = build_loop_context(loop_session, iteration)
            result = _run_loop_iteration(
                goal=goal,
                cwd=cwd,
                effective_model=effective_model,
                max_turns=max_turns,
                allowed_agents=allowed_agents,
                agent_registry=agent_registry,
                pane_backend_name=pane_backend_name,
                iteration=iteration,
                loop_context=loop_context,
                verbose=verbose,
            )
            loop_store.append_iteration(loop_session.loop_id, result)
            loop_session.iterations.append(result)
            if delay > 0 and (max_iterations == 0 or iteration < max_iterations):
                console.print(f"[dim]Pausing {delay}s before next iteration...[/dim]")
                time.sleep(delay)
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Loop stopped after {iteration} iteration(s).[/yellow]")


def _print_loop_header(goal: str, max_iterations: int) -> None:
    console.print(f"\n[bold cyan]openmax loop[/bold cyan]  [dim]{goal[:60]}[/dim]")
    if max_iterations:
        console.print(f"[dim]Max iterations: {max_iterations}[/dim]")
    else:
        console.print("[dim]Running indefinitely — Ctrl+C to stop[/dim]")
    console.print()


def _run_loop_iteration(
    *,
    goal: str,
    cwd: str,
    effective_model: str,
    max_turns: int,
    allowed_agents: list[str] | None,
    agent_registry,
    pane_backend_name: str | None,
    iteration: int,
    loop_context: str | None,
    verbose: bool = False,
) -> LoopIteration:
    started_at = utc_now_iso()
    pane_mgr = PaneManager(backend_name=pane_backend_name)
    cleaned_up = False

    def _do_cleanup() -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        try:
            pane_mgr.cleanup_all()
        except Exception:
            pass

    atexit.register(_do_cleanup)
    try:
        plan = run_lead_agent(
            task=goal,
            pane_mgr=pane_mgr,
            cwd=cwd,
            model=effective_model,
            max_turns=max_turns,
            allowed_agents=allowed_agents,
            agent_registry=agent_registry,
            loop_context=loop_context,
            verbose=verbose,
        )
        return _make_loop_iteration(iteration, started_at, plan)
    except LeadAgentStartupError as exc:
        console.print(f"[red]Iteration {iteration} failed to start: {exc}[/red]")
        return _make_loop_iteration(iteration, started_at, None)
    finally:
        _do_cleanup()
        atexit.unregister(_do_cleanup)


def _make_loop_iteration(iteration: int, started_at: str, plan: Any) -> LoopIteration:
    from openmax.lead_agent.types import PlanResult

    if plan is None or not isinstance(plan, PlanResult):
        return LoopIteration(
            iteration=iteration,
            session_id=None,
            started_at=started_at,
            completed_at=utc_now_iso(),
            outcome_summary="Failed to start",
            completion_pct=0,
            tasks_done=[],
            tasks_failed=[],
        )
    done = [st.name for st in plan.subtasks if st.status == TaskStatus.DONE]
    failed = [st.name for st in plan.subtasks if st.status == TaskStatus.ERROR]
    total = len(plan.subtasks)
    pct = int(len(done) / total * 100) if total else 100
    summary = f"{len(done)}/{total} subtasks done" if total else "Completed (no subtasks)"
    return LoopIteration(
        iteration=iteration,
        session_id=None,
        started_at=started_at,
        completed_at=utc_now_iso(),
        outcome_summary=summary,
        completion_pct=pct,
        tasks_done=done,
        tasks_failed=failed,
    )


@main.command()
def panes() -> None:
    """List all terminal panes (kaku, ghostty, or tmux)."""
    if not is_kaku_available() and not is_ghostty_available() and not is_tmux_available():
        console.print("[red]No pane backend available.[/red]\nRun inside Kaku, Ghostty, or tmux.")
        raise SystemExit(1)

    all_panes = PaneManager.list_all_panes()
    console.print(f"[bold]Found {len(all_panes)} panes:[/bold]")
    for p in all_panes:
        active = " [green]★[/green]" if p.is_active else ""
        console.print(
            f"  Pane {p.pane_id}: {p.title or '(untitled)'} [dim]({p.cols}x{p.rows})[/dim]{active}"
        )


def _display_panes_table(panes_list: list) -> None:
    """Print a rich table of panes grouped by window."""
    from collections import defaultdict

    by_window: dict[int, list] = defaultdict(list)
    for p in panes_list:
        by_window[p.window_id].append(p)

    t = _make_table(title=f"Existing panes ({len(panes_list)} total)")
    t.add_column("Window", style=get_theme().cli_col_dim)
    t.add_column("Pane")
    t.add_column("Title")
    t.add_column("CWD")
    t.add_column("")

    for wid in sorted(by_window):
        for p in by_window[wid]:
            t.add_row(
                str(wid),
                str(p.pane_id),
                p.title or "(untitled)",
                p.cwd or "",
                "[green]★[/green]" if p.is_active else "",
            )
    console.print(t)


_PANE_SNAPSHOT_LINES = 30
_PANE_SNAPSHOT_CHARS = 1500


def _snapshot_panes(pane_mgr: PaneManager, panes_list: list) -> dict[int, str]:
    """Read last output of each pane; silently skip unreadable ones."""
    out: dict[int, str] = {}
    for p in panes_list:
        try:
            text = pane_mgr.get_text(p.pane_id)
            tail = "\n".join(text.splitlines()[-_PANE_SNAPSHOT_LINES:])
            out[p.pane_id] = tail[-_PANE_SNAPSHOT_CHARS:]
        except Exception:
            out[p.pane_id] = "(unreadable)"
    return out


def _attached_panes_context(panes_list: list, contents: dict[int, str] | None = None) -> str:
    """Build a text block describing existing panes (with output snapshots) for the lead agent."""
    from collections import defaultdict

    by_window: dict[int, list] = defaultdict(list)
    for p in panes_list:
        by_window[p.window_id].append(p)

    lines = [
        "## Attached Existing Panes",
        "These panes were already running when `manage` was invoked.",
        "Use read_pane_output / send_text_to_pane to interact with them.",
    ]
    for wid in sorted(by_window):
        lines.append(f"\nWindow {wid}:")
        for p in by_window[wid]:
            active = " [ACTIVE]" if p.is_active else ""
            title = p.title or "(untitled)"
            lines.append(f"  pane_id={p.pane_id}  title={title!r}  cwd={p.cwd}{active}")
            if contents and p.pane_id in contents:
                lines.append("  ```")
                lines.extend(f"  {ln}" for ln in contents[p.pane_id].splitlines())
                lines.append("  ```")
    return "\n".join(lines)


@main.command()
@click.argument("task", required=False, default=None)
@click.option("--cwd", default=None, help="Working directory context")
@click.option("--model", default=None, help="Model for the lead agent")
@click.option(
    "--max-turns", default=None, type=click.IntRange(min=1), help="Max turns (default: unlimited)"
)
@click.option("--keep-panes", is_flag=True, default=False, help="Don't close new panes on exit")
@click.option("--agents", default=None, help="Comma-separated list of allowed agent names")
@click.option(
    "--pane-backend",
    "pane_backend_name",
    type=click.Choice(["kaku", "ghostty", "tmux", "headless", "auto"], case_sensitive=False),
    default=None,
    help="Pane backend to use (defaults to auto-detect)",
)
@click.option(
    "--no-confirm",
    is_flag=True,
    default=False,
    help="Skip interactive plan confirmation (for automation)",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show detailed subtask output")
def manage(
    task: str | None,
    cwd: str | None,
    model: str | None,
    max_turns: int,
    keep_panes: bool,
    agents: str | None,
    pane_backend_name: str | None,
    no_confirm: bool,
    verbose: bool,
) -> None:
    """Discover all existing terminal panes and optionally manage them with TASK.

    Without TASK: show a table of all running panes/windows.
    With TASK: attach existing panes and run the lead agent so it can interact with them.
    """
    if not is_kaku_available() and not is_ghostty_available() and not is_tmux_available():
        console.print("[red]No pane backend available.[/red]\nRun inside Kaku, Ghostty, or tmux.")
        raise SystemExit(1)

    pane_backend_name = resolve_pane_backend_name(pane_backend_name)
    all_panes = PaneManager.list_all_panes()
    if not all_panes:
        console.print("[yellow]No existing panes found.[/yellow]")
        return

    if not task:
        _display_panes_table(all_panes)
        return

    cwd = _resolve_cwd(cwd)
    try:
        agent_registry = load_agent_registry(cwd)
    except AgentConfigError as exc:
        raise click.UsageError(str(exc)) from exc

    allowed_agents = _parse_allowed_agents(agents, set(agent_registry.names()))

    if pane_backend_name == "kaku" and not ensure_kaku():
        raise SystemExit(1)
    if pane_backend_name == "ghostty" and not ensure_ghostty():
        raise SystemExit(1)
    if pane_backend_name == "tmux" and not ensure_tmux():
        raise SystemExit(1)

    pane_mgr = PaneManager(backend_name=pane_backend_name)

    for p in all_panes:
        pane_mgr.attach_pane(p, purpose=p.title or p.cwd or f"pane-{p.pane_id}")

    pane_contents = _snapshot_panes(pane_mgr, all_panes)
    _cleaned_up = False

    def _do_cleanup():
        nonlocal _cleaned_up
        if _cleaned_up or keep_panes:
            return
        _cleaned_up = True
        try:
            pane_mgr.cleanup_all()
        except Exception:
            pass

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
                allowed_agents=allowed_agents,
                agent_registry=agent_registry,
                loop_context=_attached_panes_context(all_panes, pane_contents),
                plan_confirm=not no_confirm,
                verbose=verbose,
            )
        except LeadAgentStartupError as exc:
            raise SystemExit(1) from exc
        else:
            done_count = sum(1 for t in plan.subtasks if t.status == TaskStatus.DONE)
            console.print(f"\n[bold]Done.[/bold] {len(plan.subtasks)} sub-tasks, {done_count} done")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if not keep_panes and not _cleaned_up:
            console.print("[dim]Closing new panes...[/dim]")
            _do_cleanup()
        elif keep_panes:
            console.print("[dim]Keeping new panes open (--keep-panes).[/dim]")


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
    t = get_theme()
    tbl.add_column("Session", style=t.cli_col_bold, no_wrap=True)
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Phase", style=t.cli_col_dim, no_wrap=True)
    tbl.add_column("%", justify="right", no_wrap=True)
    tbl.add_column("Updated", style=t.cli_col_dim, no_wrap=True)
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

        status_style = _status_styles().get(meta.status, get_theme().cli_session_default)

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

    status_style = _status_styles().get(meta.status, get_theme().cli_session_default)

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
        tbl = _make_table(title="Subtasks", title_style=get_theme().cli_col_bold, expand=True)
        th = get_theme()
        tbl.add_column("Name", style=th.cli_col_bold)
        tbl.add_column("Status")
        tbl.add_column("Agent", style=th.cli_col_dim)
        tbl.add_column("Pane", justify="right", style=th.cli_col_dim)
        tbl.add_column("Elapsed", justify="right", style=th.cli_col_dim)
        tbl.add_column(
            "Notes", style=th.cli_col_dim, max_width=40, no_wrap=True, overflow="ellipsis"
        )

        for task in plan.subtasks:
            st_style = _subtask_status_styles().get(task.status, get_theme().subtask_default)
            pane_str = str(task.pane_id) if task.pane_id is not None else "-"
            elapsed = _inspect_elapsed(task)
            notes = (task.completion_notes or "")[:40] if hasattr(task, "completion_notes") else ""
            tbl.add_row(
                task.name,
                f"[{st_style}]{task.status}[/{st_style}]",
                task.agent_type,
                pane_str,
                elapsed,
                notes,
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

    tbl = _make_table(title="Session usage", title_style=get_theme().cli_col_bold, expand=True)
    tbl.add_column("Session", style=get_theme().cli_col_bold)
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
        border_style=get_theme().provider_total_border,
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
        border_style=get_theme().provider_border,
        padding=(1, 2),
    )
    console.print(panel)


def _quota_bar(used_pct: float) -> str:
    """20-char quota bar: green when plenty left, red when near limit."""
    filled = min(int(used_pct / 5), 20)
    empty = 20 - filled
    t = get_theme()
    if used_pct >= 90:
        color = t.quota_danger
    elif used_pct >= 70:
        color = t.quota_warning
    else:
        color = t.quota_ok
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
    """Set up Claude authentication and register openMax MCP servers.

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

        if _claude_openmax_mcp_registered():
            console.print(f"[green]Claude Code MCP:[/green] registered in {_claude_config_path()}")
        else:
            console.print(
                f"[yellow]Claude Code MCP:[/yellow] not registered in {_claude_config_path()}"
            )

        codex_registered = _codex_openmax_mcp_registered()
        if codex_registered:
            console.print(f"[green]Codex MCP:[/green] registered in {_codex_config_path()}")
        elif shutil.which("codex"):
            console.print(f"[yellow]Codex MCP:[/yellow] not registered in {_codex_config_path()}")
        else:
            console.print("[dim]Codex MCP:[/dim] skipped (codex CLI not found)")
        return

    # Skip if already authenticated
    already_ok, detail = has_claude_auth()
    if already_ok:
        console.print(f"[green]Already authenticated:[/green] {detail}")
    else:
        if not shutil.which("claude"):
            console.print("[red]claude CLI not found.[/red]")
            console.print("Install it first: https://docs.anthropic.com/en/docs/claude-code")
            raise SystemExit(1)

        console.print("[bold]openMax Setup[/bold]\n")
        console.print(
            "This will run [bold]claude setup-token[/bold] to configure a long-lived token.\n"
        )

        ok = run_claude_setup_token()
        if not ok:
            console.print("\n[red]Setup failed.[/red]")
            raise SystemExit(1)

    mcp_registered = _register_openmax_mcp_server()
    if mcp_registered:
        console.print(
            f"[green]Registered MCP server:[/green] {_claude_config_path()} "
            f"({_OPENMAX_MCP_SERVER_NAME})"
        )
    else:
        console.print(f"[dim]MCP server already registered:[/dim] {_claude_config_path()}")

    codex_registered = _register_openmax_codex_mcp_server()
    if codex_registered is True:
        console.print(f"[green]Registered Codex MCP server:[/green] {_codex_config_path()}")
    elif codex_registered is False:
        console.print(f"[dim]Codex MCP server already registered:[/dim] {_codex_config_path()}")
    else:
        console.print("[yellow]Codex CLI not found; skipped Codex MCP registration.[/yellow]")
    console.print("\n[green]Setup complete.[/green]")


@main.command("install-skill")
@click.option("--global", "global_", is_flag=True, default=False, help="Install globally")
@click.option("--cwd", default=None, help="Project root for project-level install")
def install_skill(global_: bool, cwd: str | None) -> None:
    """Install openMax skills (/openmax, /codex) for Claude Code.

    Default: installs to .claude/commands/ in the current project.
    With --global: installs to ~/.claude/commands/ (available in all projects).
    """
    from openmax.skills import global_commands_dir, install, project_commands_dir

    target = global_commands_dir() if global_ else project_commands_dir(cwd)
    links = install(target)
    scope = "global" if global_ else "project"
    for link in links:
        console.print(f"[green]Installed ({scope}):[/green] {link}")
    console.print("[dim]Invoke in Claude Code:[/dim] [bold]/openmax[/bold] or [bold]/codex[/bold]")


@main.command()
def models() -> None:
    """Select the lead agent model and save it to config."""
    current = get_model()
    if current:
        console.print(f"[dim]Current model:[/dim] [bold]{current}[/bold]\n")

    model_ids = fetch_anthropic_models()
    console.print()
    for i, mid in enumerate(model_ids, 1):
        marker = " [green]✓[/green]" if mid == current else ""
        console.print(f"  [bold]{i}.[/bold] {mid}{marker}")
    console.print()
    raw = input("Select model (number or paste ID): ").strip()

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


@main.command()
@click.argument("message")
@click.option("--session", required=True, envvar="OPENMAX_SESSION_ID", help="Session ID")
def msg(message: str, session: str) -> None:
    """Send a JSON message to the lead agent mailbox."""
    from openmax.mailbox import send_mailbox_message

    try:
        json.loads(message)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"MESSAGE must be valid JSON: {exc}") from exc

    try:
        send_mailbox_message(session, message)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except ConnectionRefusedError:
        click.echo("Error: lead agent not running (connection refused)", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command()
@click.option("--session", required=True, help="Session ID")
@click.option("--cwd", default=None, help="Working directory (default: current)")
def tail(session: str, cwd: str | None) -> None:
    """Stream messages from a session in follow mode."""
    from pathlib import Path as _Path

    cwd_path = _Path(cwd).resolve() if cwd else _Path.cwd()
    log_path = cwd_path / ".openmax" / f"messages-{session}.jsonl"
    if not log_path.exists():
        click.echo(f"No message log found: {log_path}", err=True)
        sys.exit(1)

    with log_path.open(encoding="utf-8") as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                try:
                    ev = json.loads(line)
                    ts = ev.pop("_ts", None)
                    ts_str = f"{ts:.1f}" if ts else ""
                    click.echo(f"[{ts_str}] {json.dumps(ev, ensure_ascii=False)}")
                except json.JSONDecodeError:
                    click.echo(line.rstrip())
            else:
                time.sleep(0.1)


@main.command()
@click.option("--session", required=True, help="Session ID")
@click.option("--cwd", default=None, help="Working directory (default: current)")
def replay(session: str, cwd: str | None) -> None:
    """Replay all messages from a completed session."""
    from pathlib import Path as _Path

    cwd_path = _Path(cwd).resolve() if cwd else _Path.cwd()
    log_path = cwd_path / ".openmax" / f"messages-{session}.jsonl"
    if not log_path.exists():
        click.echo(f"No message log found: {log_path}", err=True)
        sys.exit(1)

    start_ts: float | None = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = ev.pop("_ts", None)
        if ts is not None:
            if start_ts is None:
                start_ts = ts
            rel = ts - start_ts
            m, s = divmod(int(rel), 60)
            ts_str = f"{m:02d}:{s:02d}.{int((rel % 1) * 10)}"
        else:
            ts_str = "??:??.?"
        msg_type = ev.get("type", "?")
        task = ev.get("task", "?")
        detail = ev.get("summary") or ev.get("msg") or ""
        pct = f" {ev['pct']}%" if "pct" in ev else ""
        console.print(f"  [dim]{ts_str}[/dim]  [bold]{task}[/bold]  [{msg_type}]{pct}  {detail}")
