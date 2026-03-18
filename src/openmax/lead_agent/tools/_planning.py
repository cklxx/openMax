"""Tools for planning and task tracking."""

from __future__ import annotations

import time
from typing import Any

from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _VALID_PHASE_TRANSITIONS,
    _append_session_event,
    _pane_id_for_task,
    _persist_report_to_main,
    _read_subtask_report,
    _record_phase_anchor,
    _runtime,
    _save_pane_log,
    _synthesize_report_from_pane,
    _tool_response,
)
from openmax.lead_agent.tools._verify import _auto_merge_branch
from openmax.lead_agent.types import TaskStatus
from openmax.output import P, console
from openmax.task_file import (
    append_shared_context,
    delete_checkpoint,
    list_checkpoint_paths,
    read_checkpoint,
)


def _topological_sort_check(subtasks: list[dict[str, Any]]) -> str | None:
    """Return an error message if there is a cycle, else None."""
    name_set = {st["name"] for st in subtasks}
    adj: dict[str, list[str]] = {st["name"]: [] for st in subtasks}
    for st in subtasks:
        for dep in st.get("dependencies", []):
            if dep not in name_set:
                return f"Dependency '{dep}' of subtask '{st['name']}' does not exist"
            adj[dep].append(st["name"])

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> str | None:
        visited.add(node)
        in_stack.add(node)
        for neighbor in adj[node]:
            if neighbor in in_stack:
                return f"Circular dependency detected: {node} -> {neighbor}"
            if neighbor not in visited:
                err = dfs(neighbor)
                if err:
                    return err
        in_stack.discard(node)
        return None

    for name in adj:
        if name not in visited:
            err = dfs(name)
            if err:
                return err
    return None


def _format_plan_for_display(
    subtasks: list[dict[str, Any]],
    rationale: str,
    parallel_groups: list[list[str]],
) -> None:
    console.print("\n  [bold cyan]Proposed Plan[/bold cyan]")
    console.print(f"  [dim]{rationale}[/dim]\n")
    for i, st in enumerate(subtasks, 1):
        files_str = ", ".join(st.get("files", []))[:60]
        console.print(f"  {i}. [bold]{st['name']}[/bold] — {st['description']}")
        if files_str:
            console.print(f"     [dim]{files_str}[/dim]")
    if parallel_groups:
        groups_str = " | ".join(", ".join(g) for g in parallel_groups)
        console.print(f"\n  [dim]Parallel: {groups_str}[/dim]")


def _prompt_plan_confirmation(
    subtasks: list[dict[str, Any]],
    rationale: str,
    parallel_groups: list[list[str]],
    runtime: Any,
) -> str | None:
    """Show plan and prompt user. Returns feedback string or None if approved."""
    dashboard = runtime.dashboard
    if dashboard:
        dashboard.stop()
    _format_plan_for_display(subtasks, rationale, parallel_groups)
    try:
        raw = input("\n  Approve? [Y/n/feedback]: ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = "y"
    if dashboard:
        dashboard.start()
    if raw.lower() in ("", "y", "yes"):
        return None
    return raw


@tool(
    "submit_plan",
    "Submit a structured task decomposition before dispatching "
    "agents. Validates dependencies (no cycles) and parallel "
    "groups (no conflicts). Call this after planning and before "
    "any dispatch_agent calls.",
    {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "files": {"type": "array", "items": {"type": "string"}},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "estimated_minutes": {"type": "integer"},
                    },
                    "required": ["name", "description"],
                },
            },
            "rationale": {"type": "string"},
            "parallel_groups": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            },
        },
        "required": ["subtasks", "rationale", "parallel_groups"],
    },
)
async def submit_plan(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    subtasks_raw = args.get("subtasks", [])
    rationale = args.get("rationale", "")
    parallel_groups = args.get("parallel_groups", [])

    if not subtasks_raw:
        return _tool_response({"error": "No subtasks provided"})

    cycle_err = _topological_sort_check(subtasks_raw)
    if cycle_err:
        return _tool_response({"error": cycle_err})

    all_names = {st["name"] for st in subtasks_raw}
    for group in parallel_groups:
        for name in group:
            if name not in all_names:
                return _tool_response({"error": f"Parallel group member '{name}' not in subtasks"})

    dep_map = {st["name"]: set(st.get("dependencies", [])) for st in subtasks_raw}
    for group in parallel_groups:
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if a in dep_map.get(b, set()) or b in dep_map.get(a, set()):
                    return _tool_response(
                        {
                            "error": (
                                f"Parallel group conflict: "
                                f"'{a}' and '{b}' have a "
                                "dependency relationship"
                            )
                        }
                    )

    file_map: dict[str, set[str]] = {}
    for st in subtasks_raw:
        files = st.get("files", [])
        if isinstance(files, list):
            file_map[st["name"]] = set(files)
    file_warnings: list[str] = []
    for group in parallel_groups:
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                overlap = file_map.get(a, set()) & file_map.get(b, set())
                if overlap:
                    file_warnings.append(
                        f"'{a}' and '{b}' share files: {', '.join(sorted(overlap))}"
                    )

    plan_data = {
        "subtasks": subtasks_raw,
        "rationale": rationale,
        "parallel_groups": parallel_groups,
    }
    console.print(f"  [bold cyan]{P}[/bold cyan]  plan: {len(subtasks_raw)} subtasks")

    if runtime.plan_confirm:
        feedback = _prompt_plan_confirmation(subtasks_raw, rationale, parallel_groups, runtime)
        if feedback is not None:
            _append_session_event("tool.submit_plan.revision_requested", plan_data)
            return _tool_response({"status": "revision_requested", "feedback": feedback})

    runtime.plan_submitted = True
    _append_session_event("tool.submit_plan", plan_data)

    result_data: dict[str, Any] = {
        "status": "accepted",
        "subtask_count": len(subtasks_raw),
        "parallel_group_count": len(parallel_groups),
    }
    if file_warnings:
        result_data["file_overlap_warnings"] = file_warnings
        for warning in file_warnings:
            console.print(f"  [yellow]![/yellow]  File overlap: {warning}")

    return _tool_response(result_data)


@tool(
    "transition_phase",
    "Transition between workflow phases. Validates from_phase matches current, "
    "gate_summary is descriptive. Records phase.anchor event and updates session phase.",
    {
        "type": "object",
        "properties": {
            "from_phase": {"type": "string"},
            "to_phase": {"type": "string"},
            "gate_summary": {"type": "string"},
            "artifacts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["from_phase", "to_phase", "gate_summary", "artifacts"],
    },
)
async def transition_phase(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    from_phase = args.get("from_phase", "").strip().lower()
    to_phase = args.get("to_phase", "").strip().lower()
    gate_summary = args.get("gate_summary", "").strip()
    artifacts = args.get("artifacts", [])

    if len(gate_summary) < 20:
        return _tool_response(
            f"Error: gate_summary must be at least 20 characters (got {len(gate_summary)})"
        )

    current = runtime.current_phase
    if from_phase != current:
        return _tool_response(
            f"Error: from_phase '{from_phase}' does not match current phase '{current}'"
        )

    allowed = _VALID_PHASE_TRANSITIONS.get(from_phase)
    if allowed is None or to_phase not in allowed:
        valid_str = ", ".join(sorted(allowed)) if allowed else "none"
        return _tool_response(
            f"Error: invalid transition '{from_phase}' \u2192 '{to_phase}'. "
            f"Valid next phases: {valid_str}"
        )

    runtime.current_phase = to_phase
    _record_phase_anchor(to_phase, gate_summary)

    if runtime.dashboard:
        runtime.dashboard.update_phase(to_phase)

    _append_session_event(
        "tool.transition_phase",
        {
            "from_phase": from_phase,
            "to_phase": to_phase,
            "gate_summary": gate_summary,
            "artifacts": artifacts,
        },
    )
    return _tool_response(f"Transitioned from '{from_phase}' to '{to_phase}'")


@tool(
    "check_checkpoints",
    "Check for pending agent checkpoints — decision forks where sub-agents are waiting. "
    "Include in every monitoring round.",
    {},
)
async def check_checkpoints(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    items = []
    for p in list_checkpoint_paths(runtime.cwd):
        task_name = p.stem
        content = read_checkpoint(runtime.cwd, task_name)
        if content is not None:
            items.append(
                {
                    "task_name": task_name,
                    "pane_id": _pane_id_for_task(task_name),
                    "content": content[:2000],
                }
            )
    _append_session_event("tool.check_checkpoints", {"pending": len(items)})
    return _tool_response({"pending_checkpoints": items, "count": len(items)})


@tool(
    "resolve_checkpoint",
    "Resolve a pending agent checkpoint: delete the file, record the decision on the "
    "blackboard, and send the decision to the agent's pane so it can continue.",
    {
        "type": "object",
        "properties": {
            "task_name": {"type": "string"},
            "decision": {"type": "string"},
        },
        "required": ["task_name", "decision"],
    },
)
async def resolve_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name, decision = args["task_name"], args["decision"]
    delete_checkpoint(runtime.cwd, task_name)
    append_shared_context(runtime.cwd, decision, section=f"Decision for {task_name}")
    pane_id = _pane_id_for_task(task_name)
    if pane_id is not None and runtime.pane_mgr.is_pane_alive(pane_id):
        runtime.pane_mgr.send_text(pane_id, decision)
    _append_session_event("tool.resolve_checkpoint", {"task_name": task_name, "pane_id": pane_id})
    return _tool_response(f"Resolved checkpoint for '{task_name}', decision sent to pane {pane_id}")


@tool(
    "mark_task_done",
    "Mark a sub-task as completed. Call only after verifying the agent has "
    "committed its changes and output looks correct.",
    {"task_name": str, "notes": str},
)
async def mark_task_done(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    notes = args.get("notes", "").strip()
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            st.status = TaskStatus.DONE
            st.finished_at = time.time()
            if st.pane_id is not None:
                _save_pane_log(runtime, st)
            report_text = _read_subtask_report(task_name)
            if report_text is None:
                report_text = _synthesize_report_from_pane(runtime, st)
            if report_text:
                st.completion_notes = report_text[:2000]
                _persist_report_to_main(runtime, task_name, report_text)
            if notes:
                st.completion_notes = notes
            console.print(f"  [bold green]\u2713[/bold green]  [bold]{task_name}[/bold] done")
            if runtime.dashboard is not None:
                runtime.dashboard.update_subtask(
                    task_name,
                    st.agent_type,
                    st.pane_id,
                    "done",
                    started_at=st.started_at,
                    finished_at=st.finished_at,
                )
            _append_session_event("tool.mark_task_done", {"task_name": task_name})
            merge_note = ""
            if st.branch_name:
                merge_note = _auto_merge_branch(runtime, st)
            msg = f"Marked '{task_name}' as done"
            if merge_note:
                msg += f". {merge_note}"
            return _tool_response(msg)
    return _tool_response(f"Task '{task_name}' not found")


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
    return _tool_response(f"Recorded anchor for phase '{phase}'")
