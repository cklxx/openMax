"""Core run_lead_agent function and supporting logic."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from openmax.agent_registry import AgentRegistry, built_in_agent_registry
from openmax.dashboard import create_dashboard, print_agent_text
from openmax.lead_agent.formatting import _format_tool_use, tool_category
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.lead_agent.tools import (
    ALL_TOOLS,
    _append_session_event,
)
from openmax.lead_agent.types import (
    PlanResult,
    SubTask,
    TaskStatus,
    _classify_startup_failure,
)
from openmax.output import P, console
from openmax.pane_manager import PaneManager
from openmax.project_tools import detect_project_tooling, format_tooling_block
from openmax.session_runtime import (
    ContextBuilder,
    SessionSnapshot,
    SessionStore,
)
from openmax.stats import load_stats
from openmax.usage import SessionUsage, UsageStore, usage_from_result

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_TRANSIENT_RETRIES = 5
_RATE_LIMIT_BASE_WAIT = 30  # seconds
_RATE_LIMIT_MAX_WAIT = 300  # 5 minutes cap

# API error patterns that are transient and safe to retry
_TRANSIENT_ERROR_PATTERNS = (
    "tool_use` ids must be unique",
    "overloaded",
    "rate_limit",
    "internal_server_error",
)


class _TransientAPIError(RuntimeError):
    """Raised when the lead agent run fails due to a transient API error."""


def _load_system_prompt() -> str:
    return (_PROMPT_DIR / "lead_agent.md").read_text()


def _build_lead_env() -> dict[str, str]:
    """Build env dict for the lead agent SDK client.

    Unsets CLAUDECODE to prevent nested-session errors.
    The SDK passes --setting-sources "" which skips Claude's settings files,
    so we forward API key / base URL from settings into env vars.
    """
    from openmax.auth import _read_claude_settings_env

    env: dict[str, str] = {"CLAUDECODE": ""}
    settings_env = _read_claude_settings_env()
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if key not in os.environ and key in settings_env:
            env[key] = settings_env[key]
    return env


def _subtask_cost(st: SubTask) -> float:
    """Get the best available cost for a subtask: reported > estimated."""
    if st.cost_usd > 0:
        return st.cost_usd
    return st.estimated_cost_usd or 0.0


def _populate_subtask_usage(usage: SessionUsage, subtasks: list[SubTask]) -> None:
    """Fill subtask_usage and total_session_cost_usd on the SessionUsage object."""
    usage.subtask_usage = [
        {
            "task_name": st.name,
            "agent_type": st.agent_type,
            "input_tokens": st.input_tokens,
            "output_tokens": st.output_tokens,
            "cost_usd": _subtask_cost(st),
            "source": st.usage_source,
        }
        for st in subtasks
    ]
    agent_cost = sum(s["cost_usd"] for s in usage.subtask_usage)
    usage.total_session_cost_usd = usage.cost_usd + agent_cost


def _print_subtask_usage(usage: SessionUsage) -> None:
    """Print a compact subtask usage breakdown to the console."""
    from rich.table import Table

    tbl = Table(show_header=True, box=None, padding=(0, 1))
    tbl.add_column("Task", style="bold")
    tbl.add_column("Agent", style="dim")
    tbl.add_column("Tokens", justify="right")
    tbl.add_column("Cost", justify="right")
    tbl.add_column("Src", style="dim")
    for s in usage.subtask_usage:
        tokens = s.get("input_tokens", 0) + s.get("output_tokens", 0)
        tbl.add_row(
            s["task_name"][:30],
            s.get("agent_type", ""),
            f"{tokens:,}" if tokens else "-",
            f"${s.get('cost_usd', 0):.4f}" if s.get("cost_usd") else "-",
            s.get("source", "est")[:3],
        )
    console.print()
    console.print("  [bold]Agent usage breakdown:[/bold]")
    console.print(tbl)
    console.print(f"  [dim]{usage.session_total_line()}[/dim]")


def _task_status_from_value(value: str) -> TaskStatus:
    normalized = value.lower()
    if normalized == TaskStatus.RUNNING.value:
        return TaskStatus.RUNNING
    if normalized == TaskStatus.DONE.value:
        return TaskStatus.DONE
    if normalized == TaskStatus.ERROR.value:
        return TaskStatus.ERROR
    return TaskStatus.PENDING


def _plan_from_snapshot(snapshot: SessionSnapshot) -> PlanResult:
    subtasks = [
        SubTask(
            name=task.name,
            agent_type=task.agent_type,
            prompt=task.prompt,
            status=_task_status_from_value(task.status),
            pane_id=task.pane_id,
        )
        for task in snapshot.plan.subtasks
    ]
    return PlanResult(goal=snapshot.plan.goal, subtasks=subtasks)


_SNAPSHOT_CHAR_CAP = 500
_SNAPSHOT_TIMEOUT = 3


def _gather_project_snapshot(cwd: str, *, minimal: bool = False) -> str:
    """Return a compact project-state block (git + dir tree). Empty on failure."""
    try:
        sections: list[str] = []

        # Git status + branch (parallel)
        kw = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        status_proc = subprocess.Popen(["git", "status", "--porcelain"], **kw)
        branch_proc = subprocess.Popen(["git", "rev-parse", "--abbrev-ref", "HEAD"], **kw)
        try:
            status_out, _ = status_proc.communicate(timeout=_SNAPSHOT_TIMEOUT)
        except subprocess.TimeoutExpired:
            status_proc.kill()
            status_out = None
        try:
            branch_out, _ = branch_proc.communicate(timeout=_SNAPSHOT_TIMEOUT)
        except subprocess.TimeoutExpired:
            branch_proc.kill()
            branch_out = None

        if status_out is not None and status_proc.returncode == 0:
            branch = branch_out.strip() if branch_out and branch_proc.returncode == 0 else "unknown"
            dirty_lines = [line for line in status_out.splitlines() if line.strip()]
            dirty_count = len(dirty_lines)
            if dirty_count == 0:
                sections.append(f"Branch: {branch} (clean)")
            else:
                sections.append(
                    f"Branch: {branch} | {dirty_count} uncommitted file"
                    f"{'s' if dirty_count != 1 else ''}"
                )
                for line in dirty_lines[:15]:
                    sections.append(f"  {line}")
                if dirty_count > 15:
                    sections.append(f"  ... and {dirty_count - 15} more")

        if minimal:
            return "\n".join(sections)

        # Shallow directory tree (depth 2, dirs only)
        tree_lines: list[str] = []
        for root, dirs, _files in os.walk(cwd):
            depth = root.replace(cwd, "").count(os.sep)
            if depth >= 2:
                dirs.clear()
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            if depth == 0:
                continue
            rel = os.path.relpath(root, cwd)
            subdirs = ", ".join(dirs[:6])
            entry = f"  {rel}/"
            if subdirs:
                entry += f" — {subdirs}"
            tree_lines.append(entry)
            if len(tree_lines) >= 12:
                break

        if tree_lines:
            sections.append("Structure:")
            sections.extend(tree_lines)

        tooling = detect_project_tooling(cwd)
        if tooling:
            sections.append("Tooling:")
            sections.append("  " + format_tooling_block(tooling).replace("\n", "\n  "))

        result = "\n".join(sections)
        return result[:_SNAPSHOT_CHAR_CAP] if result else ""
    except Exception:
        return ""


def _agent_strategy_hint(allowed: list[str]) -> str:
    has_claude = "claude-code" in allowed
    has_codex = "codex" in allowed
    if has_claude and has_codex:
        return (
            "Strategy: agent_type is auto-inferred from role "
            "(reviewer/challenger/debugger→claude-code, writer→codex). "
            "You can omit agent_type in dispatch_agent and submit_plan subtasks."
        )
    if has_codex:
        return f"Prefer '{allowed[0]}' for all tasks."
    return f"Prefer '{allowed[0]}' for all tasks."


def _match_archetype(task: str, cwd: str) -> tuple[str | None, object]:
    """Match task to an archetype. Returns (formatted_context, archetype_obj)."""
    try:
        from openmax.archetypes import format_archetype_context, get_all_archetypes, match_archetype

        archetypes = get_all_archetypes(cwd)
        match = match_archetype(task, archetypes)
        if match is None:
            return None, None
        section = format_archetype_context(match, task)
        return (section if section else None), match
    except Exception:
        return None, None


_QUALITY_MODE_PROMPT = """\
## Quality Mode (ACTIVE)

The automated quality workflow handles the full refinement cycle for every subtask:

1. WRITER implements the code (commits + merges)
2. AST checker runs locally — flags functions exceeding line limits
3. REVIEWER critiques: density, naming, composition, DRY (with AST violations)
4. CHALLENGER proposes radically simpler alternative with pseudocode
5. WRITER rewrites using reviewer + challenger + AST feedback (commits + merges)

Steps 2-5 are code-enforced — the orchestrator runs them automatically. \
The first draft is NEVER the final draft."""


def _build_lead_prompt(
    task: str,
    cwd: str,
    session_id: str | None,
    resume_context: str | None,
    allowed_agents: list[str] | None = None,
    loop_context: str | None = None,
    archetype_ctx: str | None = None,
    quality_mode: bool = False,
) -> str:
    parts = [f"## Goal\n{task}", f"Working directory: {cwd}"]

    # Project snapshot — minimal on resume (structure already known)
    snapshot = _gather_project_snapshot(cwd, minimal=bool(resume_context))
    if snapshot:
        parts.append(f"## Project State\n{snapshot}")

    if archetype_ctx:
        parts.append(archetype_ctx)

    if quality_mode:
        parts.append(_QUALITY_MODE_PROMPT)

    if allowed_agents:
        agents_str = ", ".join(allowed_agents)
        strategy = _agent_strategy_hint(allowed_agents)
        parts.append(
            f"Allowed agents: {agents_str}. Do NOT use agent types outside this list. {strategy}"
        )
    if session_id:
        parts.append(f"Session ID: {session_id}")
    if loop_context:
        parts.append(loop_context)
    if resume_context:
        parts.append("## Prior Session State (resume)\n" + resume_context)
    parts.append("Execute now. Follow the workflow in your system prompt.")
    return "\n\n".join(parts)


def run_lead_agent(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None = None,
    max_turns: int | None = None,
    session_id: str | None = None,
    resume: bool = False,
    allowed_agents: list[str] | None = None,
    agent_registry: AgentRegistry | None = None,
    loop_context: str | None = None,
    plan_confirm: bool = True,
    verbose: bool = False,
    quality_mode: bool = False,
    ui_coordinator: Any | None = None,
    max_concurrent_agents: int = 0,
    mailbox: Any | None = None,
    auto_retry: bool = False,
) -> PlanResult:
    """Run the lead agent synchronously (wraps async), with retry on transient API errors."""
    for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
        try:
            return anyio.run(
                _run_lead_agent_async,
                task,
                pane_mgr,
                cwd,
                model,
                max_turns,
                session_id,
                resume,
                allowed_agents,
                agent_registry,
                loop_context,
                plan_confirm,
                verbose,
                quality_mode,
                ui_coordinator,
                max_concurrent_agents,
                mailbox,
            )
        except _TransientAPIError:
            if attempt >= _MAX_TRANSIENT_RETRIES:
                raise
            console.print(
                f"\n  [bold yellow]⚠ Rate limited[/bold yellow]"
                f" (attempt {attempt + 1}/{_MAX_TRANSIENT_RETRIES})"
            )
            if auto_retry:
                wait = min(_RATE_LIMIT_BASE_WAIT * (2**attempt), _RATE_LIMIT_MAX_WAIT)
                console.print(f"  [dim]Auto-retrying in {wait}s...[/dim]")
                time.sleep(wait)
            else:
                console.print("  [dim]Press Enter to retry, or Ctrl+C to abort...[/dim]")
                try:
                    input()
                except (KeyboardInterrupt, EOFError):
                    raise RuntimeError("Aborted by user after rate limit")
    raise RuntimeError("unreachable")


async def _run_lead_agent_async(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None,
    max_turns: int | None,
    session_id: str | None,
    resume: bool,
    allowed_agents: list[str] | None = None,
    agent_registry: AgentRegistry | None = None,
    loop_context: str | None = None,
    plan_confirm: bool = True,
    verbose: bool = False,
    quality_mode: bool = False,
    ui_coordinator: Any | None = None,
    max_concurrent_agents: int = 0,
    external_mailbox: Any | None = None,
) -> PlanResult:
    normalized_cwd = str(Path(cwd).resolve())
    dashboard = None if ui_coordinator else create_dashboard(task, verbose=verbose)
    runtime = LeadAgentRuntime(
        cwd=cwd,
        plan=PlanResult(goal=task),
        pane_mgr=pane_mgr,
        allowed_agents=allowed_agents,
        agent_registry=agent_registry or built_in_agent_registry(),
        dashboard=dashboard,
        plan_confirm=plan_confirm,
        quality_mode=quality_mode,
        ui_coordinator=ui_coordinator,
        max_concurrent_agents=max_concurrent_agents,
    )
    runtime.session_stats = load_stats(cwd)
    token = bind_lead_agent_runtime(runtime)

    _t0 = time.monotonic()
    startup_stage = "sdk_client_startup"
    startup_complete = False
    try:
        if dashboard:
            dashboard.start()
        resume_context: str | None = None
        if session_id:
            runtime.session_store = SessionStore()
            if resume:
                snapshot = runtime.session_store.load_snapshot(session_id)
                runtime.session_meta = snapshot.meta
                runtime.session_meta.status = "active"
                runtime.session_store.save_meta(runtime.session_meta)
                runtime.plan = _plan_from_snapshot(snapshot)

                mismatch_details: list[str] = []
                if snapshot.meta.task != task:
                    mismatch_details.append(
                        f"task requested='{task}' stored='{snapshot.meta.task}'"
                    )
                if snapshot.meta.cwd != normalized_cwd:
                    mismatch_details.append(
                        f"cwd requested='{normalized_cwd}' stored='{snapshot.meta.cwd}'"
                    )
                if mismatch_details:
                    details = "; ".join(mismatch_details)
                    console.print(f"[yellow]Resuming session with mismatch:[/yellow] {details}")
                    _append_session_event(
                        "session.resume_mismatch",
                        {
                            "details": details,
                            "requested_task": task,
                            "stored_task": snapshot.meta.task,
                            "requested_cwd": normalized_cwd,
                            "stored_cwd": snapshot.meta.cwd,
                        },
                    )

                context_result = ContextBuilder().build_prompt_context(snapshot)
                resume_context = context_result.text
                if context_result.compaction_summary:
                    _append_session_event(
                        "context.compacted",
                        {"summary": context_result.compaction_summary},
                    )

                if resume and runtime.plan and hasattr(runtime.plan, "subtasks"):
                    from openmax.session_runtime import reconcile_resumed_subtasks

                    reset = reconcile_resumed_subtasks(runtime.plan, runtime.pane_mgr)
                    if reset:
                        console.print(
                            f"  [yellow]\u21ba[/yellow] Reset {len(reset)} stale subtask(s) "
                            f"to pending: {', '.join(reset)}"
                        )
                        resume_context = (resume_context or "") + (
                            f"\n\nNOTE: These tasks were running but their panes are gone"
                            f" \u2014 re-dispatch them: {', '.join(reset)}"
                        )
            else:
                runtime.session_meta = runtime.session_store.create_session(session_id, task, cwd)
                runtime.plan = PlanResult(goal=task)
                _append_session_event("session.started", {"task": task, "cwd": cwd})
                _append_session_event("user.goal_received", {"task": task})

        if session_id:
            if external_mailbox is not None:
                runtime.mailbox = external_mailbox
            else:
                from openmax.mailbox import SessionMailbox

                mailbox = SessionMailbox(
                    session_id=session_id,
                    log_dir=Path(cwd) / ".openmax",
                )
                mailbox.start()
                runtime.mailbox = mailbox

        # Create SDK MCP server with our tools
        server = create_sdk_mcp_server(
            name="openmax",
            version="0.1.0",
            tools=ALL_TOOLS,
        )

        tool_names = [f"mcp__openmax__{t.name}" for t in ALL_TOOLS]

        options = ClaudeAgentOptions(
            system_prompt=_load_system_prompt(),
            mcp_servers={"openmax": server},
            allowed_tools=tool_names,
            disallowed_tools=[
                "Read",
                "Write",
                "Edit",
                "Bash",
                "Glob",
                "Grep",
                "Agent",
                "NotebookEdit",
                "WebFetch",
                "WebSearch",
            ],
            max_turns=max_turns,
            cwd=cwd,
            permission_mode="bypassPermissions",
            env=_build_lead_env(),
        )
        if model:
            options.model = model

        # Build prompt in a background thread while SDK subprocess starts
        def _sync_build_prompt() -> tuple[str, object]:
            arch_ctx, matched = _match_archetype(task, cwd)
            p = _build_lead_prompt(
                task,
                cwd,
                session_id,
                resume_context,
                allowed_agents=allowed_agents,
                loop_context=loop_context,
                archetype_ctx=arch_ctx,
                quality_mode=quality_mode,
            )
            return p, matched

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        prompt_future = executor.submit(_sync_build_prompt)

        if dashboard:
            dashboard.update_spinner_label("connecting")

        async with ClaudeSDKClient(options=options) as client:
            # Await prompt (likely already done while SDK was connecting)
            prompt, matched_arch = await anyio.to_thread.run_sync(prompt_future.result)
            executor.shutdown(wait=False)
            runtime.matched_archetype = matched_arch

            if not ui_coordinator:
                from openmax.banner import print_banner as _print_banner

                if dashboard:
                    dashboard.update_spinner_label("thinking")
                _print_banner(session_id=session_id, resume=resume)

            startup_stage = "prompt_submission"
            await client.query(prompt)

            startup_stage = "response_stream"
            async for msg in client.receive_response():
                if not startup_complete:
                    startup_complete = True
                    if dashboard:
                        dashboard.mark_connected()
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            print_agent_text(block.text)
                            _append_session_event("lead.message", {"text": block.text})
                        elif isinstance(block, ToolUseBlock):
                            formatted = _format_tool_use(block.name, block.input)
                            cat = tool_category(block.name)
                            if dashboard:
                                dashboard.add_tool_event(formatted, cat)
                            # Show dispatch/intervention prominently;
                            # monitor (polling) and system (bookkeeping) are noise.
                            if cat == "dispatch":
                                console.print(f"  [cyan]{P}[/cyan]  [bold]{formatted}[/bold]")
                            elif cat == "intervention":
                                console.print(f"  [yellow]{P}[/yellow]  [bold]{formatted}[/bold]")
                elif isinstance(msg, ResultMessage):
                    sid = session_id or "__unnamed__"
                    usage = usage_from_result(sid, msg)
                    _populate_subtask_usage(usage, runtime.plan.subtasks)
                    if session_id:
                        UsageStore().save(usage)
                    if hasattr(msg, "usage") and msg.usage:
                        _append_session_event(
                            "usage.tokens",
                            {
                                "input_tokens": getattr(msg.usage, "input_tokens", 0),
                                "output_tokens": getattr(msg.usage, "output_tokens", 0),
                                "cache_read_tokens": getattr(msg.usage, "cache_read_tokens", 0),
                                "cache_creation_tokens": getattr(
                                    msg.usage, "cache_creation_tokens", 0
                                ),
                            },
                        )
                    start = dashboard.start_time if dashboard else _t0
                    elapsed_s = time.monotonic() - start
                    if elapsed_s >= 60:
                        m, s = divmod(int(elapsed_s), 60)
                        elapsed_str = f"{m}m {s}s"
                    else:
                        elapsed_str = f"{elapsed_s:.1f}s"
                    console.print()
                    if msg.is_error:
                        error_detail = msg.result or "unknown error"
                        console.print(
                            f"  [bold reverse red] \u2717 error [/bold reverse red]"
                            f"  {elapsed_str}"
                            f"  [dim]{usage.summary_line()}[/dim]"
                        )
                        console.print(f"  [red]{error_detail}[/red]")
                        # Raise retriable error for transient API failures
                        error_lower = error_detail.lower()
                        if any(p in error_lower for p in _TRANSIENT_ERROR_PATTERNS):
                            raise _TransientAPIError(error_detail)
                    else:
                        console.print(
                            f"  [bold reverse green] \u2713 done [/bold reverse green]"
                            f"  {elapsed_str}"
                            f"  [dim]{usage.compact_line()}[/dim]"
                        )
                    if len(usage.subtask_usage) >= 2:
                        _print_subtask_usage(usage)

        if runtime.session_meta is not None and runtime.session_store is not None:
            runtime.session_meta.status = "completed"
            runtime.session_store.save_meta(runtime.session_meta)
            _append_session_event(
                "session.completed",
                {
                    "total_subtasks": len(runtime.plan.subtasks),
                    "done_subtasks": len(
                        [t for t in runtime.plan.subtasks if t.status == TaskStatus.DONE]
                    ),
                },
            )
        return runtime.plan
    except Exception as exc:
        startup_failure = (
            None if startup_complete else _classify_startup_failure(exc, startup_stage)
        )
        if startup_failure is not None:
            console.print(f"[bold red]Error:[/bold red] {startup_failure.console_message()}")
        if runtime.session_meta is not None and runtime.session_store is not None:
            runtime.session_meta.status = "failed" if startup_failure is not None else "aborted"
            runtime.session_store.save_meta(runtime.session_meta)
            if startup_failure is not None:
                _append_session_event("session.startup_failed", startup_failure.event_payload())
            else:
                _append_session_event("session.aborted", {"reason": str(exc)})
        if startup_failure is not None:
            raise startup_failure from exc
        raise
    finally:
        if runtime.mailbox is not None and external_mailbox is None:
            runtime.mailbox.stop()
        if dashboard:
            dashboard.stop()
        reset_lead_agent_runtime(token)
