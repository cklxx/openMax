"""Core run_lead_agent function and supporting logic."""

from __future__ import annotations

from pathlib import Path

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
from rich.panel import Panel

from openmax.agent_registry import AgentRegistry, built_in_agent_registry
from openmax.dashboard import RunDashboard, console
from openmax.lead_agent.formatting import _format_tool_use, tool_category, tool_style
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
from openmax.memory import MemoryStore
from openmax.pane_manager import PaneManager
from openmax.session_runtime import (
    ContextBuilder,
    LeadAgentRuntime,
    SessionSnapshot,
    SessionStore,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.usage import UsageStore, usage_from_result

_PROMPT_DIR = Path(__file__).parent / "prompts"

_CATEGORY_ICONS = {
    "dispatch": ">>",
    "monitor": "~~",
    "intervention": "!!",
    "system": "..",
}


def _load_system_prompt() -> str:
    return (_PROMPT_DIR / "lead_agent.md").read_text()


def _build_lead_env() -> dict[str, str]:
    """Build env dict for the lead agent SDK client.

    Unsets CLAUDECODE to prevent nested-session errors.
    Auth is handled by `claude setup-token` (stored in Claude's own config).
    """
    return {"CLAUDECODE": ""}


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


def _build_lead_prompt(
    task: str,
    cwd: str,
    session_id: str | None,
    resume_context: str | None,
    memory_context: str | None,
    allowed_agents: list[str] | None = None,
) -> str:
    parts = [f"## Goal\n{task}", f"Working directory: {cwd}"]
    if allowed_agents:
        agents_str = ", ".join(allowed_agents)
        parts.append(
            f"Allowed agents: {agents_str} (prefer '{allowed_agents[0]}'). "
            f"Do NOT use agent types outside this list."
        )
    if session_id:
        parts.append(f"Session ID: {session_id}")
    if resume_context:
        parts.append("## Prior Session State (resume)\n" + resume_context)
    if memory_context:
        parts.append("## Workspace Memory\n" + memory_context)
    parts.append("Execute now. Follow the workflow in your system prompt.")
    return "\n\n".join(parts)


def run_lead_agent(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None = None,
    max_turns: int = 50,
    session_id: str | None = None,
    resume: bool = False,
    allowed_agents: list[str] | None = None,
    agent_registry: AgentRegistry | None = None,
) -> PlanResult:
    """Run the lead agent synchronously (wraps async)."""
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
    )


async def _run_lead_agent_async(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None,
    max_turns: int,
    session_id: str | None,
    resume: bool,
    allowed_agents: list[str] | None = None,
    agent_registry: AgentRegistry | None = None,
) -> PlanResult:
    normalized_cwd = str(Path(cwd).resolve())
    dashboard = RunDashboard(task)
    runtime = LeadAgentRuntime(
        cwd=cwd,
        plan=PlanResult(goal=task),
        pane_mgr=pane_mgr,
        memory_store=MemoryStore(),
        allowed_agents=allowed_agents,
        agent_registry=agent_registry or built_in_agent_registry(),
        dashboard=dashboard,
    )
    token = bind_lead_agent_runtime(runtime)

    startup_stage = "sdk_client_startup"
    startup_complete = False
    try:
        dashboard.start()
        resume_context: str | None = None
        memory_context = runtime.memory_store.build_context(cwd=cwd, task=task)
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

        prompt = _build_lead_prompt(
            task,
            cwd,
            session_id,
            resume_context,
            memory_context.text if memory_context else None,
            allowed_agents=allowed_agents,
        )

        console.print(
            Panel(
                f"[bold]Goal:[/bold] {task}"
                + (f"\n[bold]Session:[/bold] {session_id}" if session_id else "")
                + ("\n[bold]Mode:[/bold] resume" if resume else ""),
                title="openMax Lead Agent",
                border_style="blue",
            )
        )

        async with ClaudeSDKClient(options=options) as client:
            startup_stage = "prompt_submission"
            await client.query(prompt)

            startup_stage = "response_stream"
            async for msg in client.receive_response():
                startup_complete = True
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            console.print(block.text)
                            _append_session_event("lead.message", {"text": block.text})
                        elif isinstance(block, ToolUseBlock):
                            formatted = _format_tool_use(block.name, block.input)
                            cat = tool_category(block.name)
                            style = tool_style(cat)
                            icon = _CATEGORY_ICONS.get(cat, "..")
                            console.print(f"  [{style}]{icon} {formatted}[/{style}]")
                            if dashboard is not None:
                                dashboard.add_tool_event(formatted, cat)
                elif isinstance(msg, ResultMessage):
                    sid = session_id or "__unnamed__"
                    usage = usage_from_result(sid, msg)
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
                    console.print(
                        Panel(
                            usage.summary_line(),
                            title="Lead Agent Summary",
                            border_style="green",
                        )
                    )

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
            console.print(Panel(startup_failure.console_message(), border_style="red"))
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
        dashboard.stop()
        reset_lead_agent_runtime(token)
