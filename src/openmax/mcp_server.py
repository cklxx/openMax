"""MCP server for sub-agents to report progress back to the lead agent."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from openmax.mailbox import send_mailbox_payload

mcp = FastMCP(
    "openmax",
    instructions=(
        "Use report_progress while you work and report_done when your task finishes. "
        "The server reads OPENMAX_SESSION_ID from the environment and forwards updates "
        "to the openMax lead agent mailbox."
    ),
)


def _error_result(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _normalize_required_text(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _current_session_id() -> str | None:
    session_id = os.environ.get("OPENMAX_SESSION_ID", "").strip()
    return session_id or None


def _send_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = _current_session_id()
    if not session_id:
        return _error_result("OPENMAX_SESSION_ID is not set")

    try:
        send_mailbox_payload(session_id, payload)
    except (FileNotFoundError, OSError) as exc:
        return _error_result(str(exc))

    return {"ok": True, "session_id": session_id, "payload": payload}


@mcp.tool(
    description="Report that a sub-task is complete and include a one-line summary.",
    structured_output=True,
)
def report_done(task: str, summary: str) -> dict[str, Any]:
    task_name = _normalize_required_text(task)
    if task_name is None:
        return _error_result("task is required")

    summary_text = _normalize_required_text(summary)
    if summary_text is None:
        return _error_result("summary is required")

    return _send_tool_payload({"type": "done", "task": task_name, "summary": summary_text})


@mcp.tool(
    description="Report mid-task progress with a percent and a short status message.",
    structured_output=True,
)
def report_progress(task: str, pct: int, msg: str) -> dict[str, Any]:
    task_name = _normalize_required_text(task)
    if task_name is None:
        return _error_result("task is required")

    status_msg = _normalize_required_text(msg)
    if status_msg is None:
        return _error_result("msg is required")

    if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
        return _error_result("pct must be an integer between 0 and 100")

    return _send_tool_payload(
        {"type": "progress", "task": task_name, "pct": pct, "msg": status_msg}
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
