"""Lead Agent — orchestration via claude-agent-sdk with custom tools.

Custom tools (dispatch_agent, read_pane_output, etc.) run in-process
via SDK MCP server. The lead agent uses ClaudeSDKClient for interactive
multi-turn orchestration.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import anyio
from rich.console import Console
from rich.panel import Panel

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from openmax.pane_manager import PaneManager

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

SYSTEM_PROMPT = """\
You are the Lead Agent of openMax, a multi-AI-agent orchestration system.
You operate as a project manager following a strict management lifecycle:

## Phase 1: Align Goal
- Clarify the user's intent. Restate the goal precisely.
- Identify constraints, scope boundaries, and success criteria.

## Phase 2: Plan & Decompose
- Break the goal into concrete, parallelizable sub-tasks.
- For each sub-task, decide which agent type is best suited.

## Phase 3: Dispatch
- Use the `dispatch_agent` tool to assign each sub-task to an agent.
- Each agent runs interactively in its own terminal pane.

## Phase 4: Monitor & Correct
- Use `read_pane_output` to check each agent's progress.
- If an agent is stuck or off-track, use `send_text_to_pane` to intervene.
- Use `mark_task_done` when a task is finished.

## Phase 5: Summarize & Report
- When all tasks are done, use `report_completion` to finalize.
- Provide a summary of what was accomplished.

## Available agent types:
- "claude-code": Claude Code CLI — best for most coding tasks. Supports plan mode, interactive editing, full tool access.
- "codex": OpenAI Codex CLI — good for code generation and review.
- "opencode": OpenCode CLI — alternative coding assistant.
- "generic": Falls back to interactive claude session.

## Important:
- Each agent runs interactively in a kaku terminal pane — users can click in and intervene.
- Be decisive. Dispatch agents quickly after planning.
- Monitor actively — read pane output to check progress.
- Correct course when agents drift from the goal.
"""


# ── Tool definitions ──────────────────────────────────────────────

# These are module-level so they can capture the shared state via closure
_pane_mgr: PaneManager | None = None
_plan: PlanResult | None = None
_cwd: str = ""
_agent_window_id: int | None = None  # the shared window for all agent panes


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
    _plan.subtasks.append(subtask)

    # Show layout info
    win = _pane_mgr.windows.get(_agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [green]✓[/green] Dispatched [bold]{task_name}[/bold] "
        f"→ pane {pane.pane_id} ({agent_type}) "
        f"[dim][window {_agent_window_id}, {pane_count} panes][/dim]"
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({
                    "status": "dispatched",
                    "pane_id": pane.pane_id,
                    "window_id": _agent_window_id,
                    "agent_type": agent_type,
                    "task_name": task_name,
                    "panes_in_window": pane_count,
                }),
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
        return {"content": [{"type": "text", "text": text}]}
    except RuntimeError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "send_text_to_pane",
    "Send text/instructions to an agent pane, as if typed by the user. Use to give follow-up instructions or intervene.",
    {"pane_id": int, "text": str},
)
async def send_text_to_pane(args: dict[str, Any]) -> dict[str, Any]:
    pane_id = args["pane_id"]
    text = args["text"]
    _pane_mgr.send_text(pane_id, text + "\n")
    console.print(f"  [yellow]→[/yellow] Sent to pane {pane_id}: {text[:80]}")
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
            return {"content": [{"type": "text", "text": f"Marked '{task_name}' as done"}]}
    return {"content": [{"type": "text", "text": f"Task '{task_name}' not found"}]}


@tool(
    "report_completion",
    "Report overall goal completion percentage and summary. Call when all tasks are done.",
    {"completion_pct": int, "notes": str},
)
async def report_completion(args: dict[str, Any]) -> dict[str, Any]:
    pct = args["completion_pct"]
    notes = args["notes"]
    console.print(Panel(
        f"[bold]Completion: {pct}%[/bold]\n{notes}",
        title="Progress Report",
        border_style="cyan",
    ))
    return {"content": [{"type": "text", "text": f"Reported {pct}% — {notes}"}]}


# ── Run the lead agent ────────────────────────────────────────────


def run_lead_agent(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None = None,
    max_turns: int = 50,
) -> PlanResult:
    """Run the lead agent synchronously (wraps async)."""
    return anyio.run(_run_lead_agent_async, task, pane_mgr, cwd, model, max_turns)


async def _run_lead_agent_async(
    task: str,
    pane_mgr: PaneManager,
    cwd: str,
    model: str | None,
    max_turns: int,
) -> PlanResult:
    global _pane_mgr, _plan, _cwd, _agent_window_id

    _pane_mgr = pane_mgr
    _plan = PlanResult(goal=task)
    _cwd = cwd
    _agent_window_id = None

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
            report_completion,
        ],
    )

    tool_names = [
        "mcp__openmax__dispatch_agent",
        "mcp__openmax__read_pane_output",
        "mcp__openmax__send_text_to_pane",
        "mcp__openmax__list_managed_panes",
        "mcp__openmax__mark_task_done",
        "mcp__openmax__report_completion",
    ]

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"openmax": server},
        allowed_tools=tool_names,
        disallowed_tools=[
            "Read", "Write", "Edit", "Bash", "Glob", "Grep",
            "Agent", "NotebookEdit", "WebFetch", "WebSearch",
        ],
        max_turns=max_turns,
        cwd=cwd,
        permission_mode="bypassPermissions",
        env={"CLAUDECODE": ""},
    )
    if model:
        options.model = model

    prompt = (
        f"Goal: {task}\n\n"
        f"Working directory: {cwd}\n\n"
        "Proceed through the management lifecycle:\n"
        "1. Align goal\n"
        "2. Plan & decompose\n"
        "3. Dispatch agents\n"
        "4. Monitor & correct\n"
        "5. Summarize & report"
    )

    console.print(Panel(
        f"[bold]Goal:[/bold] {task}",
        title="openMax Lead Agent",
        border_style="blue",
    ))

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)

        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        console.print(block.text)
                    elif isinstance(block, ToolUseBlock):
                        console.print(f"  [dim]⚙ {block.name}[/dim]")
            elif isinstance(msg, ResultMessage):
                cost = msg.total_cost_usd or 0
                console.print(Panel(
                    f"Cost: ${cost:.4f}\n"
                    f"Duration: {msg.duration_ms / 1000:.1f}s\n"
                    f"Turns: {msg.num_turns}",
                    title="Lead Agent Summary",
                    border_style="green",
                ))

    return _plan
