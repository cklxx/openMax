"""Text formatting and tool-use display helpers."""

from __future__ import annotations

from typing import Any

_TOOL_NAME_PREFIX = "mcp__openmax__"

_TOOL_CATEGORIES: dict[str, str] = {
    "dispatch_agent": "dispatch",
    "get_agent_recommendations": "system",
    "run_command": "system",
    "submit_plan": "system",
    "run_verification": "system",
    "read_pane_output": "monitor",
    "list_managed_panes": "monitor",
    "find_files": "monitor",
    "grep_files": "monitor",
    "read_file": "monitor",
    "send_text_to_pane": "intervention",
    "ask_user": "intervention",
    "mark_task_done": "system",
    "record_phase_anchor": "system",
    "transition_phase": "system",
    "remember_learning": "system",
    "check_conflicts": "system",
    "report_completion": "system",
    "wait": "system",
}

_CATEGORY_STYLES: dict[str, str] = {
    "dispatch": "bold",
    "monitor": "dim",
    "intervention": "bold",
    "system": "dim",
}


def tool_category(tool_name: str) -> str:
    """Return the category for a tool name (dispatch/monitor/intervention/system)."""
    normalized = tool_name.removeprefix(_TOOL_NAME_PREFIX)
    return _TOOL_CATEGORIES.get(normalized, "system")


def tool_style(category: str) -> str:
    """Return the Rich style string for a tool category."""
    return _CATEGORY_STYLES.get(category, "dim")


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

    if normalized == "ask_user":
        question = str(tool_input.get("question", "")).strip()
        choices = tool_input.get("choices") or []
        suffix = f" ({len(choices)} choices)" if choices else ""
        if question:
            return f"Asking user{suffix}: {_truncate_text(question)}"
        return "Asking user a question"

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

    if normalized == "submit_plan":
        subtasks = tool_input.get("subtasks", [])
        count = len(subtasks) if isinstance(subtasks, list) else 0
        return f"Submitting plan with {count} subtasks"

    if normalized == "run_command":
        command = str(tool_input.get("command", "")).strip()
        task_name = str(tool_input.get("task_name", "")).strip()
        label = task_name or command
        return f"Running command: {_truncate_text(label)}"

    if normalized == "run_verification":
        check_type = str(tool_input.get("check_type", "")).strip()
        command = str(tool_input.get("command", "")).strip()
        label = check_type or command
        return f"Running verification: {_truncate_text(label)}"

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

    if normalized == "find_files":
        pattern = str(tool_input.get("pattern", "")).strip()
        return f"Finding files: {pattern}" if pattern else "Finding files"

    if normalized == "grep_files":
        pattern = str(tool_input.get("pattern", "")).strip()
        return f"Searching: {_truncate_text(pattern)}" if pattern else "Searching files"

    if normalized == "read_file":
        path = str(tool_input.get("path", "")).strip()
        return f"Reading {path}" if path else "Reading a file"

    if normalized == "check_conflicts":
        return "Checking for git conflicts"

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

    if normalized == "transition_phase":
        from_p = str(tool_input.get("from_phase", "")).strip()
        to_p = str(tool_input.get("to_phase", "")).strip()
        return f"Transitioning phase: {from_p} \u2192 {to_p}"

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
