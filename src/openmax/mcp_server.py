"""MCP server — progress reporting + Codex execution tools."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from openmax.mailbox import send_mailbox_payload

log = logging.getLogger(__name__)

mcp = FastMCP(
    "openmax",
    instructions=(
        "Use report_progress while you work and report_done when your task finishes. "
        "Pass the session_id from your task brief when available. "
        "If omitted, the server falls back to OPENMAX_SESSION_ID from the environment "
        "and forwards updates to the openMax lead agent mailbox."
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


def _resolve_session_id(explicit: str) -> str | None:
    normalized = _normalize_required_text(explicit)
    if normalized is not None:
        return normalized
    return _current_session_id()


def _send_tool_payload(
    payload: dict[str, Any],
    session_id: str = "",
    *,
    soft_fail: bool = False,
) -> dict[str, Any]:
    session_id = _resolve_session_id(session_id)
    if not session_id:
        if soft_fail:
            log.warning("report_progress called without session_id — progress not forwarded")
            return {"ok": True, "warning": "no session_id, progress not forwarded"}
        return _error_result(
            "session_id is required: pass it as a parameter, or ensure "
            "OPENMAX_SESSION_ID is set in the environment"
        )

    try:
        send_mailbox_payload(session_id, payload)
    except (FileNotFoundError, OSError) as exc:
        if soft_fail:
            log.warning("report_progress delivery failed: %s", exc)
            return {"ok": True, "warning": f"delivery failed: {exc}"}
        return _error_result(str(exc))

    return {"ok": True, "session_id": session_id, "payload": payload}


@mcp.tool(
    description=(
        "Report that a sub-task is complete. "
        "Pass session_id from the '## Your Task (openMax)' section of your prompt."
    ),
    structured_output=True,
)
def report_done(
    task: str,
    summary: str,
    session_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    task_name = _normalize_required_text(task)
    if task_name is None:
        return _error_result("task is required")

    summary_text = _normalize_required_text(summary)
    if summary_text is None:
        return _error_result("summary is required")

    payload: dict[str, Any] = {"type": "done", "task": task_name, "summary": summary_text}
    if input_tokens > 0 or output_tokens > 0:
        payload["input_tokens"] = max(input_tokens, 0)
        payload["output_tokens"] = max(output_tokens, 0)
        payload["cost_usd"] = max(cost_usd, 0.0)

    return _send_tool_payload(payload, session_id)


@mcp.tool(
    description=(
        "Report progress on a sub-task. "
        "Pass session_id from the '## Your Task (openMax)' section of your prompt."
    ),
    structured_output=True,
)
def report_progress(task: str, pct: int, msg: str, session_id: str = "") -> dict[str, Any]:
    task_name = _normalize_required_text(task)
    if task_name is None:
        return _error_result("task is required")

    status_msg = _normalize_required_text(msg)
    if status_msg is None:
        return _error_result("msg is required")

    if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
        return _error_result("pct must be an integer between 0 and 100")

    return _send_tool_payload(
        {"type": "progress", "task": task_name, "pct": pct, "msg": status_msg},
        session_id,
        soft_fail=True,
    )


_CODEX_APPROVAL_MODES = frozenset({"full-auto", "suggest"})


@mcp.tool(
    description=(
        "Execute a coding task with Codex CLI. "
        "Claude plans the approach, then delegates implementation to Codex. "
        "Returns the full output and exit status."
    ),
    structured_output=True,
)
def execute_with_codex(
    task: str,
    cwd: str = "",
    approval_mode: str = "full-auto",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    task_text = _normalize_required_text(task)
    if task_text is None:
        return _error_result("task is required")
    if not shutil.which("codex"):
        return _error_result("codex CLI not found. Install: npm i -g @openai/codex")
    if approval_mode not in _CODEX_APPROVAL_MODES:
        return _error_result(f"invalid approval_mode: {approval_mode!r}")

    effective_cwd = cwd.strip() or os.getcwd()
    return _run_codex(task_text, effective_cwd, approval_mode, timeout_seconds)


def _run_codex(task: str, cwd: str, approval_mode: str, timeout: int) -> dict[str, Any]:
    cmd = ["codex", "exec"]
    if approval_mode == "full-auto":
        cmd.append("--full-auto")
    cmd.append(task)
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "error": f"timed out after {timeout}s",
        }
    elapsed = round(time.monotonic() - start, 2)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "output": output,
        "exit_code": proc.returncode,
        "elapsed_seconds": elapsed,
        "error": "" if ok else f"exit code {proc.returncode}",
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
