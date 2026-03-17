"""Tools for shared context/blackboard."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _runtime,
    _tool_response,
)
from openmax.task_file import append_shared_context, read_shared_context


@tool(
    "update_shared_context",
    "Append an update to the shared blackboard visible to all agents. "
    "Use after key architectural decisions so subsequent agents inherit context.",
    {
        "type": "object",
        "properties": {
            "update": {"type": "string"},
            "section": {"type": "string"},
        },
        "required": ["update"],
    },
)
async def update_shared_context(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    path = append_shared_context(runtime.cwd, args["update"], args.get("section"))
    _append_session_event("tool.update_shared_context", {"section": args.get("section")})
    return _tool_response(f"Appended to {path.relative_to(runtime.cwd)}")


@tool(
    "read_shared_context",
    "Read the shared blackboard. Call before dispatching dependent agents "
    "to include relevant prior decisions in their briefs.",
    {},
)
async def read_shared_context_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    content = read_shared_context(runtime.cwd)
    _append_session_event("tool.read_shared_context", {"chars": len(content) if content else 0})
    return _tool_response({"shared_context": content[:8000] if content else None})
