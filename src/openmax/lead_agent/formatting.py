"""Text formatting and tool-use display helpers."""

from __future__ import annotations

import json
from typing import Any

_TOOL_NAME_PREFIX = "mcp__openmax__"

_TOOL_CATEGORIES: dict[str, str] = {
    "dispatch_agent": "dispatch",
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
    "merge_agent_branch": "system",
    "mark_task_done": "system",
    "record_phase_anchor": "system",
    "transition_phase": "system",
    "check_conflicts": "system",
    "report_completion": "system",
    "wait": "system",
    "update_shared_context": "system",
    "read_shared_context": "monitor",
    "check_checkpoints": "monitor",
    "resolve_checkpoint": "intervention",
    "wait_for_agent_message": "monitor",
    "read_task_report": "monitor",
}

_CATEGORY_STYLES: dict[str, str] = {
    "dispatch": "bold",
    "monitor": "dim",
    "intervention": "bold",
    "system": "dim",
}


def tool_category(tool_name: str) -> str:
    normalized = tool_name.removeprefix(_TOOL_NAME_PREFIX)
    return _TOOL_CATEGORIES.get(normalized, "system")


def tool_style(category: str) -> str:
    return _CATEGORY_STYLES.get(category, "dim")


def _truncate_text(value: str, limit: int = 100) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _format_phase_name(phase: str) -> str:
    normalized = phase.strip().lower().replace("_", " ")
    aliases = {"align": "goal alignment", "plan": "planning", "report": "final report"}
    return aliases.get(normalized, normalized or "workflow")


def _coerce_tool_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# Format spec: tool_name -> (template, [key_fields])
# Template placeholders: {0}, {1}, etc. map to key_fields values
_TOOL_FORMATS: dict[str, tuple[str, list[str]]] = {
    "dispatch_agent": ("Starting {0} via {1}", ["task_name", "agent_type"]),
    "read_pane_output": ("Checking pane {0}", ["pane_id"]),
    "send_text_to_pane": ("Sending to pane {0}", ["pane_id"]),
    "mark_task_done": ("Marking {0} done", ["task_name"]),
    "merge_agent_branch": ("Merging branch for {0}", ["task_name"]),
    "run_command": ("Running: {0}", ["command"]),
    "run_verification": ("Verifying: {0}", ["check_type"]),
    "find_files": ("Finding: {0}", ["pattern"]),
    "grep_files": ("Searching: {0}", ["pattern"]),
    "read_file": ("Reading {0}", ["path"]),
    "transition_phase": ("{0} → {1}", ["from_phase", "to_phase"]),
    "resolve_checkpoint": ("Resolving checkpoint for {0}", ["task_name"]),
    "update_shared_context": ("Updating blackboard", []),
    "read_shared_context": ("Reading blackboard", []),
    "check_checkpoints": ("Checking checkpoints", []),
    "check_conflicts": ("Checking git conflicts", []),
    "list_managed_panes": ("Reviewing panes", []),
    "wait_for_agent_message": ("Waiting for agent message", []),
    "read_task_report": ("Reading report for {0}", ["task_name"]),
}


def _format_tool_use(tool_name: str, tool_input: dict[str, Any] | None = None) -> str:
    normalized = tool_name.removeprefix(_TOOL_NAME_PREFIX)
    inp = tool_input or {}

    # Special cases that need richer logic
    if normalized == "ask_user":
        question = str(inp.get("question", "")).strip()
        choices = inp.get("choices") or []
        if isinstance(choices, str):
            try:
                choices = json.loads(choices)
            except (json.JSONDecodeError, ValueError):
                choices = [choices]
        suffix = f" ({len(choices)} choices)" if choices else ""
        return f"Asking user{suffix}: {_truncate_text(question)}" if question else "Asking user"

    if normalized == "submit_plan":
        subtasks = inp.get("subtasks", [])
        count = len(subtasks) if isinstance(subtasks, list) else 0
        return f"Submitting plan with {count} subtasks"

    if normalized == "report_completion":
        pct = _coerce_tool_int(inp.get("completion_pct"))
        notes = str(inp.get("notes", "")).strip()
        suffix = f" ({pct}%)" if pct is not None else ""
        return f"Completion{suffix}: {_truncate_text(notes)}" if notes else f"Completion{suffix}"

    if normalized == "wait":
        seconds = _coerce_tool_int(inp.get("seconds"))
        return f"Waiting {seconds}s" if seconds else "Waiting"

    if normalized == "record_phase_anchor":
        phase = _format_phase_name(str(inp.get("phase", "")))
        pct = _coerce_tool_int(inp.get("completion_pct"))
        suffix = f" ({pct}%)" if pct is not None else ""
        return f"Anchor: {phase}{suffix}"

    # Data-driven formatting
    spec = _TOOL_FORMATS.get(normalized)
    if spec:
        template, keys = spec
        values = [_truncate_text(str(inp.get(k, ""))) for k in keys]
        try:
            return template.format(*values) if values else template
        except (IndexError, KeyError):
            pass

    # Generic fallback
    fallback = normalized.replace("_", " ").strip() or tool_name
    return fallback[:1].upper() + fallback[1:]
