"""All @tool functions for the lead agent MCP server."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import tool
from rich.panel import Panel

from openmax.dashboard import console
from openmax.lead_agent.formatting import _format_phase_name, _truncate_text
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.memory import serialize_subtasks
from openmax.pane_manager import PaneManager
from openmax.session_runtime import (
    LeadAgentRuntime,
    anchor_payload,
    get_lead_agent_runtime,
    serialize_tasks,
)


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


async def _wait_for_pane_ready(
    pane_mgr: PaneManager,
    pane_id: int,
    ready_patterns: list[str],
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> bool:
    """Poll pane output until a ready pattern appears or timeout."""
    if not ready_patterns:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = pane_mgr.get_text(pane_id)
        except Exception:
            text = ""
        if any(pat in text for pat in ready_patterns):
            return True
        await anyio.sleep(poll_interval)
    return False


def _extract_smart_output(text: str, tail_lines: int = 100) -> str:
    """Return tail of output, with error lines from earlier surfaced at top."""
    lines = text.splitlines()
    tail = lines[-tail_lines:]
    error_kw = ["Error", "error", "Traceback", "FAILED", "fatal", "exception", "\u274c"]
    error_context = [
        f"[ERROR] {line.strip()}"
        for line in lines[:-tail_lines]
        if any(k in line for k in error_kw)
    ][-20:]
    if error_context:
        return "\n".join(error_context) + "\n---\n" + "\n".join(tail)
    return "\n".join(tail)


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
    "The prompt is the agent's ONLY context — include file paths, constraints, "
    "and any knowledge it cannot discover on its own. "
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
                f"  [yellow]\u26a0[/yellow] Agent '{agent_type}' not allowed, "
                f"using '{fallback}' instead"
            )
            agent_type = fallback

    adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        fallback = runtime.agent_registry.default_agent_name()
        if fallback is None:
            raise RuntimeError("No agents are configured")
        console.print(
            f"  [yellow]\u26a0[/yellow] Agent '{agent_type}' not configured, "
            f"using '{fallback}' instead"
        )
        agent_type = fallback
        adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        raise RuntimeError(f"Agent '{agent_type}' is unavailable")

    cmd_spec = adapter.get_command(prompt, cwd=runtime.cwd)
    launch_env = cmd_spec.env or None

    if runtime.agent_window_id is None:
        # First agent -> create a new window
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
        # Subsequent agents -> add pane to the same window (auto layout)
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

    # For interactive agents, wait for CLI ready then send initial prompt
    if cmd_spec.interactive and cmd_spec.initial_input:
        if cmd_spec.ready_patterns:
            ready = await _wait_for_pane_ready(
                runtime.pane_mgr,
                pane.pane_id,
                cmd_spec.ready_patterns,
                timeout=max(cmd_spec.ready_delay_seconds * 4, 30.0),
            )
            if not ready:
                console.print(
                    f"  [yellow]\u26a0[/yellow] Pane {pane.pane_id} ({agent_type}) "
                    "did not show ready signal within timeout \u2014 sending prompt anyway"
                )
        else:
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
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(task_name, agent_type, pane.pane_id, "running")

    # Show layout info
    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [green]\u2713[/green] Dispatched [bold]{task_name}[/bold] "
        f"\u2192 pane {pane.pane_id} ({agent_type}) "
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
    "Read the current terminal output of an agent pane (~100 tail lines). "
    "Error lines from earlier output are surfaced at the top with [ERROR] prefix. "
    "Look for done signals (committed, summary), errors (Traceback, FAILED), "
    "or stuck signals (same output, unanswered question).",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    try:
        text = runtime.pane_mgr.get_text(pane_id)
        text = _extract_smart_output(text, tail_lines=100)
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
    "Send text to an agent pane. Use to answer agent questions, correct drift, "
    "or give follow-up instructions. Text is pasted and submitted automatically.",
    {"pane_id": int, "text": str},
)
async def send_text_to_pane(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    text = args["text"]
    runtime.pane_mgr.send_text(pane_id, text)
    console.print(f"  [yellow]\u2192[/yellow] Sent to pane {pane_id}: {text[:80]}")
    if runtime.dashboard is not None:
        runtime.dashboard.update_pane_activity(pane_id, text[:80])
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
    "Mark a sub-task as completed. Call only after verifying the agent has "
    "committed its changes and output looks correct.",
    {"task_name": str},
)
async def mark_task_done(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            st.status = TaskStatus.DONE
            console.print(f"  [green]\u2713\u2713[/green] [bold]{task_name}[/bold] done")
            if runtime.dashboard is not None:
                runtime.dashboard.update_subtask(task_name, st.agent_type, st.pane_id, "done")
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
    runtime = _runtime()
    if runtime.dashboard is not None:
        runtime.dashboard.update_phase(phase, completion_pct)
    suffix = f" ({completion_pct}%)" if completion_pct is not None else ""
    preview = _truncate_text(summary)
    message = f"  [cyan]\u21ba[/cyan] Saved {_format_phase_name(phase)} checkpoint{suffix}"
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
    console.print(f"  [magenta]\U0001f9e0[/magenta] Learned: {lesson[:80]}")
    return {"content": [{"type": "text", "text": "Stored reusable lesson"}]}


@tool(
    "report_completion",
    "Report overall goal completion percentage and summary. Call exactly once "
    "when all tasks are done. Describe what was delivered, not what was attempted. "
    "This saves a run summary to workspace memory.",
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
    return {"content": [{"type": "text", "text": f"Reported {pct}% \u2014 {notes}"}]}


@tool(
    "ask_user",
    "Ask the human operator a question and wait for their answer. "
    "Use when the goal is genuinely ambiguous or you need a decision only the user can make. "
    "Do NOT use for routine confirmations.",
    {"question": str},
)
async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    question = args["question"]

    # Pause the dashboard so the prompt is visible
    if runtime.dashboard is not None:
        runtime.dashboard.stop()

    console.print(
        Panel(
            f"[bold yellow]Lead agent asks:[/bold yellow]\n{question}",
            border_style="yellow",
        )
    )
    answer: str = await anyio.to_thread.run_sync(lambda: input("Your answer: "))

    # Resume the dashboard
    if runtime.dashboard is not None:
        runtime.dashboard.start()

    _append_session_event(
        "tool.ask_user",
        {"question": question, "answer": answer},
    )
    return {"content": [{"type": "text", "text": answer}]}


@tool(
    "wait",
    "Wait for a specified number of seconds before continuing. "
    "Use between monitoring rounds: 10-15s for simple tasks, 20-30s for complex ones. "
    "Increase if the agent is making steady progress.",
    {"seconds": int},
)
async def wait_tool(args: dict[str, Any]) -> dict[str, Any]:
    seconds = min(max(args.get("seconds", 30), 5), 120)
    console.print(f"  [dim]\u23f3 Waiting {seconds}s...[/dim]")
    await anyio.sleep(seconds)
    return {"content": [{"type": "text", "text": f"Waited {seconds}s"}]}


@tool(
    "run_command",
    "Run any CLI command in a terminal pane. Works for both one-shot commands "
    "(e.g. 'npm test', 'cargo build', 'git log') and interactive programs "
    "(e.g. 'python', 'htop', 'psql'). Set interactive=true for long-running "
    "or interactive programs, false (default) for one-shot commands. "
    "The pane stays in the shared window "
    "and can be monitored with read_pane_output / send_text_to_pane.",
    {
        "command": str,
        "task_name": str,
        "interactive": bool,
    },
)
async def run_command(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    command_str = args["command"]
    task_name = args.get("task_name", command_str[:40])
    interactive = args.get("interactive", False)

    import shlex

    try:
        cmd_list = shlex.split(command_str)
    except ValueError:
        cmd_list = [command_str]

    if not cmd_list:
        return {"content": [{"type": "text", "text": "Error: empty command"}]}

    if runtime.agent_window_id is None:
        pane = runtime.pane_mgr.create_window(
            command=cmd_list,
            purpose=task_name,
            agent_type="command",
            title=f"openMax: {runtime.plan.goal[:40]}",
            cwd=runtime.cwd,
        )
        runtime.agent_window_id = pane.window_id
    else:
        pane = runtime.pane_mgr.add_pane(
            window_id=runtime.agent_window_id,
            command=cmd_list,
            purpose=task_name,
            agent_type="command",
            cwd=runtime.cwd,
        )

    subtask = SubTask(
        name=task_name,
        agent_type="command",
        prompt=command_str,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
    )
    _upsert_subtask(subtask)
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(task_name, "command", pane.pane_id, "running")

    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    mode = "interactive" if interactive else "one-shot"
    console.print(
        f"  [green]\u2713[/green] Running [{mode}] [bold]{command_str[:60]}[/bold] "
        f"\u2192 pane {pane.pane_id} "
        f"[dim][window {runtime.agent_window_id}, {pane_count} panes][/dim]"
    )
    _append_session_event(
        "tool.run_command",
        {
            "command": command_str,
            "task_name": task_name,
            "interactive": interactive,
            "pane_id": pane.pane_id,
            "window_id": runtime.agent_window_id,
        },
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "launched",
                        "pane_id": pane.pane_id,
                        "window_id": runtime.agent_window_id,
                        "command": command_str,
                        "task_name": task_name,
                        "interactive": interactive,
                        "panes_in_window": pane_count,
                    }
                ),
            }
        ]
    }


@tool(
    "read_file",
    "Read a file from the working directory. Use to understand codebase before planning. "
    "Returns file content (max 2000 lines). Specify offset/limit for large files.",
    {"path": str, "offset": int, "limit": int},
)
async def read_file_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    rel_path = args["path"]
    offset = args.get("offset", 0)
    limit = args.get("limit", 2000)

    # Resolve relative to cwd, prevent path traversal
    target = (Path(runtime.cwd) / rel_path).resolve()
    cwd_resolved = Path(runtime.cwd).resolve()
    if not str(target).startswith(str(cwd_resolved)):
        return {"content": [{"type": "text", "text": "Error: path outside working directory"}]}

    try:
        text = target.read_text(errors="replace")
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"Error: file not found: {rel_path}"}]}
    except IsADirectoryError:
        return {"content": [{"type": "text", "text": f"Error: {rel_path} is a directory"}]}
    except OSError as e:
        return {"content": [{"type": "text", "text": f"Error reading file: {e}"}]}

    lines = text.splitlines()
    total = len(lines)
    selected = lines[offset : offset + limit]
    numbered = [f"{i + offset + 1:>5}  {line}" for i, line in enumerate(selected)]
    header = f"# {rel_path} ({total} lines total"
    if offset:
        header += f", showing from line {offset + 1}"
    header += ")\n"
    result = header + "\n".join(numbered)

    console.print(f"  [dim]\U0001f4c4 Read {rel_path} ({len(selected)}/{total} lines)[/dim]")
    return {"content": [{"type": "text", "text": result}]}


# All tool objects for easy collection
ALL_TOOLS = [
    ask_user,
    dispatch_agent,
    get_agent_recommendations,
    read_file_tool,
    read_pane_output,
    run_command,
    send_text_to_pane,
    list_managed_panes,
    mark_task_done,
    record_phase_anchor,
    remember_learning,
    report_completion,
    wait_tool,
]
