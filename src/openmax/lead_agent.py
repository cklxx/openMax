"""Lead Agent — orchestration via claude-agent-sdk with custom tools.

Custom tools (dispatch_agent, read_pane_output, etc.) run in-process
via SDK MCP server. The lead agent uses ClaudeSDKClient for interactive
multi-turn orchestration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
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
    tool,
)
from rich.console import Console
from rich.panel import Panel

from openmax.agent_registry import AgentRegistry, built_in_agent_registry
from openmax.memory_system import MemoryStore, serialize_subtasks
from openmax.pane_manager import PaneManager
from openmax.session_runtime import (
    ContextBuilder,
    LeadAgentRuntime,
    SessionMeta,
    SessionSnapshot,
    SessionStore,
    anchor_payload,
    bind_lead_agent_runtime,
    get_lead_agent_runtime,
    reset_lead_agent_runtime,
    serialize_tasks,
)

console = Console()
_TOOL_NAME_PREFIX = "mcp__openmax__"


# ── Data types ────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class SubTask:
    name: str
    agent_type: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    pane_id: int | None = None


@dataclass
class PlanResult:
    goal: str
    subtasks: list[SubTask] = field(default_factory=list)


@dataclass
class LeadAgentStartupError(RuntimeError):
    category: str
    stage: str
    detail: str
    remediation: str

    def __post_init__(self) -> None:
        super().__init__(self.detail)

    @property
    def heading(self) -> str:
        if self.category == "authentication":
            return "Lead agent authentication failed"
        if self.category == "bootstrap":
            return "Lead agent bootstrap failed"
        return "Lead agent startup failed"

    def console_message(self) -> str:
        return (
            f"[bold red]{self.heading}[/bold red]\n"
            f"Stage: {self.stage}\n"
            f"Details: {self.detail}\n"
            f"Remediation: {self.remediation}"
        )

    def event_payload(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "stage": self.stage,
            "detail": self.detail,
            "remediation": self.remediation,
        }


# ── System prompt ─────────────────────────────────────────────────

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPT_DIR / "lead_agent.md").read_text()


def _truncate_text(value: str, limit: int = 72) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_phase_name(phase: str) -> str:
    normalized = phase.strip().lower().replace("_", " ")
    phase_aliases = {
        "align": "goal alignment",
        "plan": "planning",
        "dispatch": "agent dispatch",
        "monitor": "monitoring",
        "report": "final report",
    }
    return phase_aliases.get(normalized, normalized or "workflow")


def _format_completion_suffix(completion_pct: int | None) -> str:
    if completion_pct is None:
        return ""
    return f" ({completion_pct}%)"


def _coerce_tool_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _format_tool_use(tool_name: str, tool_input: dict[str, Any] | None = None) -> str:
    normalized = tool_name.removeprefix(_TOOL_NAME_PREFIX)
    tool_input = tool_input or {}

    if normalized == "dispatch_agent":
        task_name = str(tool_input.get("task_name", "")).strip() or "sub-task"
        agent_type = str(tool_input.get("agent_type", "")).strip() or "default agent"
        return f"Starting agent for {task_name} via {agent_type}"

    if normalized == "get_agent_recommendations":
        task = str(tool_input.get("task", "")).strip()
        return (
            f"Checking best agent for {_truncate_text(task)}"
            if task
            else "Checking which agent fits best"
        )

    if normalized == "read_pane_output":
        pane_id = tool_input.get("pane_id")
        return (
            f"Checking progress in pane {pane_id}"
            if pane_id is not None
            else "Checking agent progress"
        )

    if normalized == "send_text_to_pane":
        pane_id = tool_input.get("pane_id")
        text = str(tool_input.get("text", "")).strip()
        preview = _truncate_text(text, limit=56)
        if pane_id is not None and preview:
            return f"Sending follow-up to pane {pane_id}: {preview}"
        if pane_id is not None:
            return f"Sending follow-up to pane {pane_id}"
        return "Sending follow-up to an agent"

    if normalized == "list_managed_panes":
        return "Reviewing active panes"

    if normalized == "mark_task_done":
        task_name = str(tool_input.get("task_name", "")).strip()
        return f"Marking {task_name} done" if task_name else "Marking a sub-task done"

    if normalized == "record_phase_anchor":
        phase = _format_phase_name(str(tool_input.get("phase", "")))
        summary = str(tool_input.get("summary", "")).strip()
        suffix = _format_completion_suffix(_coerce_tool_int(tool_input.get("completion_pct")))
        if summary:
            return f"Saving {phase} checkpoint{suffix}: {_truncate_text(summary)}"
        return f"Saving {phase} checkpoint{suffix}"

    if normalized == "remember_learning":
        lesson = str(tool_input.get("lesson", "")).strip()
        return (
            f"Saving reusable lesson: {_truncate_text(lesson)}"
            if lesson
            else "Saving reusable lesson"
        )

    if normalized == "report_completion":
        completion_pct = _coerce_tool_int(tool_input.get("completion_pct"))
        notes = str(tool_input.get("notes", "")).strip()
        suffix = _format_completion_suffix(completion_pct)
        if notes:
            return f"Publishing completion update{suffix}: {_truncate_text(notes)}"
        return f"Publishing completion update{suffix}".strip()

    if normalized == "wait":
        seconds = _coerce_tool_int(tool_input.get("seconds"))
        return (
            f"Waiting {seconds}s before the next check"
            if seconds
            else "Waiting before the next check"
        )

    fallback = normalized.replace("_", " ").strip() or tool_name
    return fallback[:1].upper() + fallback[1:]


# ── Tool definitions ──────────────────────────────────────────────

def _runtime() -> LeadAgentRuntime:
    return get_lead_agent_runtime()


def _append_session_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    runtime = _runtime()
    if runtime.session_store is None or runtime.session_meta is None:
        return
    runtime.session_store.append_event(runtime.session_meta, event_type, payload)


def _update_session_phase(phase: str | None) -> None:
    runtime = _runtime()
    if runtime.session_store is None or runtime.session_meta is None or not phase:
        return
    runtime.session_meta.latest_phase = phase
    runtime.session_store.save_meta(runtime.session_meta)


def _upsert_subtask(subtask: SubTask) -> None:
    runtime = _runtime()
    if runtime.plan is None:
        raise RuntimeError("Lead agent plan is not initialized")
    for index, existing in enumerate(runtime.plan.subtasks):
        if existing.name == subtask.name:
            runtime.plan.subtasks[index] = subtask
            return
    runtime.plan.subtasks.append(subtask)


def _record_phase_anchor(phase: str, summary: str, completion_pct: int | None = None) -> None:
    runtime = _runtime()
    if runtime.plan is None:
        return
    normalized_phase = phase.strip().lower()
    payload = anchor_payload(
        phase=normalized_phase,
        summary=summary.strip(),
        tasks=serialize_tasks(runtime.plan.subtasks),
        completion_pct=completion_pct,
    )
    _append_session_event("phase.anchor", payload)
    _update_session_phase(normalized_phase)


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
    parts = [f"Goal: {task}", f"Working directory: {cwd}"]
    if allowed_agents:
        parts.append(
            f"Allowed agents (in preference order): {', '.join(allowed_agents)}. "
            f"Use '{allowed_agents[0]}' as the default unless another is better suited. "
            f"Do NOT use agent types outside this list."
        )
    if session_id:
        parts.append(f"Session ID: {session_id}")
    if resume_context:
        parts.append("Recovered session context:\n" + resume_context)
    if memory_context:
        parts.append(memory_context)
    parts.append(
        "Proceed through the management lifecycle:\n"
        "1. Align goal\n"
        "2. Plan & decompose\n"
        "3. Dispatch agents\n"
        "4. Monitor & correct\n"
        "5. Summarize & report\n\n"
        "Use `record_phase_anchor` at the end of each phase "
        "with a concise summary and current state."
    )
    return "\n\n".join(parts)


def _remember_run_summary(notes: str, completion_pct: int) -> None:
    runtime = _runtime()
    if runtime.memory_store is None or runtime.plan is None:
        return
    anchors: list[dict[str, Any]] = []
    if runtime.session_store is not None and runtime.session_meta is not None:
        for event in runtime.session_store.load_events(runtime.session_meta.session_id):
            if event.event_type == "phase.anchor":
                anchors.append(event.payload)
    runtime.memory_store.record_run_summary(
        cwd=runtime.cwd,
        task=runtime.plan.goal,
        notes=notes,
        completion_pct=completion_pct,
        subtasks=serialize_subtasks(runtime.plan.subtasks),
        anchors=anchors,
    )


def _classify_startup_failure(exc: Exception, stage: str) -> LeadAgentStartupError | None:
    detail = " ".join(str(exc).split()).strip() or exc.__class__.__name__
    normalized = detail.lower()

    auth_markers = (
        "auth",
        "login",
        "logged out",
        "unauthorized",
        "forbidden",
        "credential",
        "api key",
        "access token",
        "token expired",
        "permission denied",
        "401",
        "403",
    )
    bootstrap_markers = (
        "bootstrap",
        "startup",
        "start",
        "initialize",
        "initialise",
        "handshake",
        "failed to launch",
        "failed to start",
        "timed out",
        "timeout",
        "connection refused",
        "broken pipe",
        "transport",
    )

    if any(marker in normalized for marker in auth_markers):
        return LeadAgentStartupError(
            category="authentication",
            stage=stage,
            detail=detail,
            remediation=(
                "Refresh Claude authentication in this shell, then retry. "
                "If needed, run `claude auth login` and confirm the account has access."
            ),
        )

    if any(marker in normalized for marker in bootstrap_markers):
        return LeadAgentStartupError(
            category="bootstrap",
            stage=stage,
            detail=detail,
            remediation=(
                "Verify the Claude CLI can start cleanly in this environment, then retry. "
                "Check local shell setup, network access, and any required agent tooling."
            ),
        )

    if stage != "response_stream":
        return LeadAgentStartupError(
            category="startup",
            stage=stage,
            detail=detail,
            remediation=(
                "Retry after confirming the Claude CLI starts successfully in this shell. "
                "If the problem persists, inspect local environment and dependency setup."
            ),
        )

    return None


@tool(
    "get_agent_recommendations",
    "Get ranked agent recommendations for a task based on workspace memory and similar code work.",
    {"task": str},
)
async def get_agent_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task = args["task"]
    if runtime.memory_store is None:
        return {"content": [{"type": "text", "text": "[]"}]}
    rankings = runtime.memory_store.derive_agent_rankings(cwd=runtime.cwd, task=task)
    payload = [
        {
            "agent_type": item.agent_type,
            "score": item.score,
            "reasons": item.reasons,
        }
        for item in rankings
    ]
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


@tool(
    "dispatch_agent",
    "Dispatch a sub-task to an AI agent in a terminal pane. "
    "All agents share one window with smart grid layout. Returns pane_id.",
    {
        "task_name": str,
        "agent_type": str,
        "prompt": str,
    },
)
async def dispatch_agent(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    agent_type = args.get("agent_type", "claude-code")
    prompt = args["prompt"]

    # Enforce allowed agents constraint
    if runtime.allowed_agents:
        if agent_type not in runtime.allowed_agents:
            fallback = runtime.allowed_agents[0]
            console.print(
                f"  [yellow]⚠[/yellow] Agent '{agent_type}' not allowed, using '{fallback}' instead"
            )
            agent_type = fallback

    adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        fallback = runtime.agent_registry.default_agent_name()
        if fallback is None:
            raise RuntimeError("No agents are configured")
        console.print(
            f"  [yellow]⚠[/yellow] Agent '{agent_type}' not configured, using '{fallback}' instead"
        )
        agent_type = fallback
        adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        raise RuntimeError(f"Agent '{agent_type}' is unavailable")

    cmd_spec = adapter.get_command(prompt, cwd=runtime.cwd)
    launch_env = cmd_spec.env or None

    if runtime.agent_window_id is None:
        # First agent → create a new window
        pane_kwargs = {
            "command": cmd_spec.launch_cmd,
            "purpose": task_name,
            "agent_type": agent_type,
            "title": f"openMax: {runtime.plan.goal[:40]}",
            "cwd": runtime.cwd,
        }
        if launch_env:
            pane_kwargs["env"] = launch_env
        pane = runtime.pane_mgr.create_window(**pane_kwargs)
        runtime.agent_window_id = pane.window_id
    else:
        # Subsequent agents → add pane to the same window (auto layout)
        pane_kwargs = {
            "window_id": runtime.agent_window_id,
            "command": cmd_spec.launch_cmd,
            "purpose": task_name,
            "agent_type": agent_type,
            "cwd": runtime.cwd,
        }
        if launch_env:
            pane_kwargs["env"] = launch_env
        pane = runtime.pane_mgr.add_pane(**pane_kwargs)

    # For interactive agents, send the initial prompt after CLI starts
    if cmd_spec.interactive and cmd_spec.initial_input:
        await anyio.sleep(cmd_spec.ready_delay_seconds)
        runtime.pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)

    subtask = SubTask(
        name=task_name,
        agent_type=agent_type,
        prompt=prompt,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
    )
    _upsert_subtask(subtask)

    # Show layout info
    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [green]✓[/green] Dispatched [bold]{task_name}[/bold] "
        f"→ pane {pane.pane_id} ({agent_type}) "
        f"[dim][window {runtime.agent_window_id}, {pane_count} panes][/dim]"
    )
    _append_session_event(
        "tool.dispatch_agent",
        {
            "task_name": task_name,
            "agent_type": agent_type,
            "prompt": prompt,
            "pane_id": pane.pane_id,
            "window_id": runtime.agent_window_id,
            "panes_in_window": pane_count,
        },
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "dispatched",
                        "pane_id": pane.pane_id,
                        "window_id": runtime.agent_window_id,
                        "agent_type": agent_type,
                        "task_name": task_name,
                        "panes_in_window": pane_count,
                    }
                ),
            }
        ]
    }


@tool(
    "read_pane_output",
    "Read the current terminal output of an agent pane to check progress.",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    try:
        text = runtime.pane_mgr.get_text(pane_id)
        lines = text.splitlines()
        if len(lines) > 150:
            text = "\n".join(lines[-150:])
        _append_session_event(
            "tool.read_pane_output",
            {
                "pane_id": pane_id,
                "preview": text[:500],
            },
        )
        return {"content": [{"type": "text", "text": text}]}
    except RuntimeError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "send_text_to_pane",
    "Send text to an agent pane. Use to give follow-up instructions or intervene.",
    {"pane_id": int, "text": str},
)
async def send_text_to_pane(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    text = args["text"]
    runtime.pane_mgr.send_text(pane_id, text)
    console.print(f"  [yellow]→[/yellow] Sent to pane {pane_id}: {text[:80]}")
    _append_session_event(
        "tool.send_text_to_pane",
        {
            "pane_id": pane_id,
            "text": text,
        },
    )
    return {"content": [{"type": "text", "text": f"Sent to pane {pane_id}"}]}


@tool(
    "list_managed_panes",
    "List all managed panes and their current states.",
    {},
)
async def list_managed_panes(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    runtime.pane_mgr.refresh_states()
    summary = runtime.pane_mgr.summary()
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]}


@tool(
    "mark_task_done",
    "Mark a sub-task as completed.",
    {"task_name": str},
)
async def mark_task_done(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            st.status = TaskStatus.DONE
            console.print(f"  [green]✓✓[/green] [bold]{task_name}[/bold] done")
            _append_session_event("tool.mark_task_done", {"task_name": task_name})
            return {"content": [{"type": "text", "text": f"Marked '{task_name}' as done"}]}
    return {"content": [{"type": "text", "text": f"Task '{task_name}' not found"}]}


@tool(
    "record_phase_anchor",
    "Persist a concise workflow anchor when a lifecycle phase is completed.",
    {"phase": str, "summary": str, "completion_pct": int},
)
async def record_phase_anchor(args: dict[str, Any]) -> dict[str, Any]:
    phase = args["phase"]
    summary = args["summary"]
    completion_pct = args.get("completion_pct")
    _record_phase_anchor(phase, summary, completion_pct)
    suffix = f" ({completion_pct}%)" if completion_pct is not None else ""
    preview = _truncate_text(summary)
    message = f"  [cyan]↺[/cyan] Saved {_format_phase_name(phase)} checkpoint{suffix}"
    if preview:
        message += f": {preview}"
    console.print(message)
    return {"content": [{"type": "text", "text": f"Recorded anchor for phase '{phase}'"}]}


@tool(
    "remember_learning",
    "Store a reusable lesson so future runs in this workspace can improve automatically.",
    {"lesson": str, "rationale": str, "confidence": int},
)
async def remember_learning(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    lesson = args["lesson"]
    rationale = args.get("rationale", "")
    confidence = args.get("confidence")
    if runtime.memory_store is None:
        return {"content": [{"type": "text", "text": "Memory store unavailable"}]}
    runtime.memory_store.record_lesson(
        cwd=runtime.cwd,
        task=runtime.plan.goal,
        lesson=lesson,
        rationale=rationale,
        confidence=confidence,
    )
    console.print(f"  [magenta]🧠[/magenta] Learned: {lesson[:80]}")
    return {"content": [{"type": "text", "text": "Stored reusable lesson"}]}


@tool(
    "report_completion",
    "Report overall goal completion percentage and summary. Call when all tasks are done.",
    {"completion_pct": int, "notes": str},
)
async def report_completion(args: dict[str, Any]) -> dict[str, Any]:
    pct = args["completion_pct"]
    notes = args["notes"]
    console.print(
        Panel(
            f"[bold]Completion: {pct}%[/bold]\n{notes}",
            title="Progress Report",
            border_style="cyan",
        )
    )
    _append_session_event(
        "tool.report_completion",
        {
            "completion_pct": pct,
            "notes": notes,
        },
    )
    _record_phase_anchor("report", notes, pct)
    _remember_run_summary(notes, pct)
    return {"content": [{"type": "text", "text": f"Reported {pct}% — {notes}"}]}


@tool(
    "wait",
    "Wait for a specified number of seconds before continuing. "
    "Use this between monitoring checks to avoid excessive polling.",
    {"seconds": int},
)
async def wait_tool(args: dict[str, Any]) -> dict[str, Any]:
    seconds = min(max(args.get("seconds", 30), 5), 120)
    console.print(f"  [dim]⏳ Waiting {seconds}s...[/dim]")
    await anyio.sleep(seconds)
    return {"content": [{"type": "text", "text": f"Waited {seconds}s"}]}


# ── Run the lead agent ────────────────────────────────────────────


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
    runtime = LeadAgentRuntime(
        cwd=cwd,
        plan=PlanResult(goal=task),
        pane_mgr=pane_mgr,
        memory_store=MemoryStore(),
        allowed_agents=allowed_agents,
        agent_registry=agent_registry or built_in_agent_registry(),
    )
    token = bind_lead_agent_runtime(runtime)

    startup_stage = "sdk_client_startup"
    startup_complete = False
    try:
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
                    mismatch_details.append(f"task requested='{task}' stored='{snapshot.meta.task}'")
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
            else:
                runtime.session_meta = runtime.session_store.create_session(session_id, task, cwd)
                runtime.plan = PlanResult(goal=task)
                _append_session_event("session.started", {"task": task, "cwd": cwd})
                _append_session_event("user.goal_received", {"task": task})

        # Create SDK MCP server with our tools
        server = create_sdk_mcp_server(
            name="openmax",
            version="0.1.0",
            tools=[
                dispatch_agent,
                get_agent_recommendations,
                read_pane_output,
                send_text_to_pane,
                list_managed_panes,
                mark_task_done,
                record_phase_anchor,
                remember_learning,
                report_completion,
                wait_tool,
            ],
        )

        tool_names = [
            "mcp__openmax__dispatch_agent",
            "mcp__openmax__get_agent_recommendations",
            "mcp__openmax__read_pane_output",
            "mcp__openmax__send_text_to_pane",
            "mcp__openmax__list_managed_panes",
            "mcp__openmax__mark_task_done",
            "mcp__openmax__record_phase_anchor",
            "mcp__openmax__remember_learning",
            "mcp__openmax__report_completion",
            "mcp__openmax__wait",
        ]

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
            env={"CLAUDECODE": ""},
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
                            console.print(f"  [dim]⚙ {formatted}[/dim]")
                elif isinstance(msg, ResultMessage):
                    cost = msg.total_cost_usd or 0
                    console.print(
                        Panel(
                            f"Cost: ${cost:.4f}\n"
                            f"Duration: {msg.duration_ms / 1000:.1f}s\n"
                            f"Turns: {msg.num_turns}",
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
        startup_failure = None if startup_complete else _classify_startup_failure(exc, startup_stage)
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
        reset_lead_agent_runtime(token)
