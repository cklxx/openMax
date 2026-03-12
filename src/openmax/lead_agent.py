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

from openmax.memory_system import MemoryStore, serialize_subtasks
from openmax.pane_manager import PaneManager
from openmax.session_runtime import (
    ContextBuilder,
    SessionMeta,
    SessionSnapshot,
    SessionStore,
    anchor_payload,
    serialize_tasks,
)

console = Console()


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


# ── System prompt ─────────────────────────────────────────────────

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPT_DIR / "lead_agent.md").read_text()


# ── Tool definitions ──────────────────────────────────────────────

# These are module-level so they can capture the shared state via closure
_pane_mgr: PaneManager | None = None
_plan: PlanResult | None = None
_cwd: str = ""
_agent_window_id: int | None = None  # the shared window for all agent panes
_session_store: SessionStore | None = None
_session_meta: SessionMeta | None = None
_memory_store: MemoryStore | None = None


def _append_session_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    if _session_store is None or _session_meta is None:
        return
    _session_store.append_event(_session_meta, event_type, payload)


def _update_session_phase(phase: str | None) -> None:
    if _session_store is None or _session_meta is None or not phase:
        return
    _session_meta.latest_phase = phase
    _session_store.save_meta(_session_meta)


def _upsert_subtask(subtask: SubTask) -> None:
    for index, existing in enumerate(_plan.subtasks):
        if existing.name == subtask.name:
            _plan.subtasks[index] = subtask
            return
    _plan.subtasks.append(subtask)


def _record_phase_anchor(phase: str, summary: str, completion_pct: int | None = None) -> None:
    normalized_phase = phase.strip().lower()
    payload = anchor_payload(
        phase=normalized_phase,
        summary=summary.strip(),
        tasks=serialize_tasks(_plan.subtasks),
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
) -> str:
    parts = [f"Goal: {task}", f"Working directory: {cwd}"]
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
    if _memory_store is None:
        return
    anchors: list[dict[str, Any]] = []
    if _session_store is not None and _session_meta is not None:
        for event in _session_store.load_events(_session_meta.session_id):
            if event.event_type == "phase.anchor":
                anchors.append(event.payload)
    _memory_store.record_run_summary(
        cwd=_cwd,
        task=_plan.goal,
        notes=notes,
        completion_pct=completion_pct,
        subtasks=serialize_subtasks(_plan.subtasks),
        anchors=anchors,
    )


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
    from openmax.adapters import (
        ClaudeCodeAdapter,
        CodexAdapter,
        OpenCodeAdapter,
        SubprocessAdapter,
    )

    adapters = {
        "claude-code": ClaudeCodeAdapter(),
        "codex": CodexAdapter(),
        "opencode": OpenCodeAdapter(),
        "generic": SubprocessAdapter("generic", ["claude"]),
    }

    task_name = args["task_name"]
    agent_type = args.get("agent_type", "claude-code")
    prompt = args["prompt"]

    global _agent_window_id

    adapter = adapters.get(agent_type, adapters["generic"])
    cmd_spec = adapter.get_command(prompt, cwd=_cwd)

    if _agent_window_id is None:
        # First agent → create a new window
        pane = _pane_mgr.create_window(
            command=cmd_spec.launch_cmd,
            purpose=task_name,
            agent_type=agent_type,
            title=f"openMax: {_plan.goal[:40]}",
            cwd=_cwd,
        )
        _agent_window_id = pane.window_id
    else:
        # Subsequent agents → add pane to the same window (auto layout)
        pane = _pane_mgr.add_pane(
            window_id=_agent_window_id,
            command=cmd_spec.launch_cmd,
            purpose=task_name,
            agent_type=agent_type,
            cwd=_cwd,
        )

    # For interactive agents, send the initial prompt after CLI starts
    if cmd_spec.interactive and cmd_spec.initial_input:
        await anyio.sleep(3)
        _pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)

    subtask = SubTask(
        name=task_name,
        agent_type=agent_type,
        prompt=prompt,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
    )
    _upsert_subtask(subtask)

    # Show layout info
    win = _pane_mgr.windows.get(_agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [green]✓[/green] Dispatched [bold]{task_name}[/bold] "
        f"→ pane {pane.pane_id} ({agent_type}) "
        f"[dim][window {_agent_window_id}, {pane_count} panes][/dim]"
    )
    _append_session_event(
        "tool.dispatch_agent",
        {
            "task_name": task_name,
            "agent_type": agent_type,
            "prompt": prompt,
            "pane_id": pane.pane_id,
            "window_id": _agent_window_id,
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
                        "window_id": _agent_window_id,
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
    pane_id = args["pane_id"]
    try:
        text = _pane_mgr.get_text(pane_id)
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
    pane_id = args["pane_id"]
    text = args["text"]
    _pane_mgr.send_text(pane_id, text + "\n")
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
    _pane_mgr.refresh_states()
    summary = _pane_mgr.summary()
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]}


@tool(
    "mark_task_done",
    "Mark a sub-task as completed.",
    {"task_name": str},
)
async def mark_task_done(args: dict[str, Any]) -> dict[str, Any]:
    task_name = args["task_name"]
    for st in _plan.subtasks:
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
    console.print(f"  [cyan]↺[/cyan] Anchored phase [bold]{phase}[/bold]")
    return {"content": [{"type": "text", "text": f"Recorded anchor for phase '{phase}'"}]}


@tool(
    "remember_learning",
    "Store a reusable lesson so future runs in this workspace can improve automatically.",
    {"lesson": str, "rationale": str, "confidence": int},
)
async def remember_learning(args: dict[str, Any]) -> dict[str, Any]:
    lesson = args["lesson"]
    rationale = args.get("rationale", "")
    confidence = args.get("confidence")
    if _memory_store is None:
        return {"content": [{"type": "text", "text": "Memory store unavailable"}]}
    _memory_store.record_lesson(
        cwd=_cwd,
        task=_plan.goal,
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
) -> PlanResult:
    """Run the lead agent synchronously (wraps async)."""
    return anyio.run(
        _run_lead_agent_async, task, pane_mgr, cwd, model, max_turns, session_id, resume
    )


async def _run_lead_agent_async(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None,
    max_turns: int,
    session_id: str | None,
    resume: bool,
) -> PlanResult:
    global _pane_mgr, _plan, _cwd, _agent_window_id, _session_store, _session_meta, _memory_store

    _pane_mgr = pane_mgr
    _cwd = cwd
    normalized_cwd = str(Path(cwd).resolve())
    _agent_window_id = None
    _session_store = None
    _session_meta = None
    _memory_store = MemoryStore()

    resume_context: str | None = None
    memory_context = _memory_store.build_context(cwd=cwd, task=task)
    if session_id:
        _session_store = SessionStore()
        if resume:
            snapshot = _session_store.load_snapshot(session_id)
            _session_meta = snapshot.meta
            _session_meta.status = "active"
            _session_store.save_meta(_session_meta)
            _plan = _plan_from_snapshot(snapshot)

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
            _session_meta = _session_store.create_session(session_id, task, cwd)
            _plan = PlanResult(goal=task)
            _append_session_event("session.started", {"task": task, "cwd": cwd})
            _append_session_event("user.goal_received", {"task": task})
    else:
        _plan = PlanResult(goal=task)

    # Create SDK MCP server with our tools
    server = create_sdk_mcp_server(
        name="openmax",
        version="0.1.0",
        tools=[
            dispatch_agent,
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

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            console.print(block.text)
                            _append_session_event("lead.message", {"text": block.text})
                        elif isinstance(block, ToolUseBlock):
                            console.print(f"  [dim]⚙ {block.name}[/dim]")
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

        if _session_meta is not None and _session_store is not None:
            _session_meta.status = "completed"
            _session_store.save_meta(_session_meta)
            _append_session_event(
                "session.completed",
                {
                    "total_subtasks": len(_plan.subtasks),
                    "done_subtasks": len(
                        [t for t in _plan.subtasks if t.status == TaskStatus.DONE]
                    ),
                },
            )
        return _plan
    except Exception as exc:
        if _session_meta is not None and _session_store is not None:
            _session_meta.status = "aborted"
            _session_store.save_meta(_session_meta)
            _append_session_event("session.aborted", {"reason": str(exc)})
        raise
