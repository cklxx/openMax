"""Tools for planning and task tracking."""

from __future__ import annotations

import time
import traceback
from pathlib import PurePosixPath
from typing import Any

import anyio
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
from openmax.lead_agent.types import TaskStatus
from openmax.output import P, console
from openmax.stats import load_stats
from openmax.task_file import (
    append_shared_context,
    delete_checkpoint,
    list_checkpoint_paths,
    read_checkpoint,
)


def _roots_from_plan(
    subtasks: list[dict[str, Any]],
    parallel_groups: list[list[str]],
) -> list[dict[str, Any]]:
    """Return subtasks that have no dependencies (can be dispatched immediately)."""
    has_deps = set()
    for st in subtasks:
        for dep in st.get("dependencies", []):
            has_deps.add(st["name"])
    return [st for st in subtasks if st["name"] not in has_deps]


def _register_all_subtasks(runtime: Any, subtasks_raw: list[dict[str, Any]]) -> None:
    """Create PENDING SubTask entries for every planned subtask upfront."""
    from openmax.lead_agent.tools._helpers import _upsert_subtask as _upsert
    from openmax.lead_agent.types import SubTask

    for st in subtasks_raw:
        subtask = SubTask(
            name=st["name"],
            agent_type=st.get("agent_type", "claude-code"),
            prompt=st.get("prompt") or st["description"],
            status=TaskStatus.PENDING,
            dependencies=list(st.get("dependencies", [])),
        )
        _upsert(subtask)


async def dispatch_ready_dependents(runtime: Any) -> list[dict[str, Any]]:
    """Find PENDING tasks whose deps are all DONE and dispatch them."""
    from openmax.lead_agent.tools._dispatch import dispatch_agent

    done_names = {st.name for st in runtime.plan.subtasks if st.status == TaskStatus.DONE}
    ready = [
        st
        for st in runtime.plan.subtasks
        if st.status == TaskStatus.PENDING
        and st.dependencies
        and all(dep in done_names for dep in st.dependencies)
    ]
    if not ready:
        return []

    goal = getattr(runtime.plan, "goal", "") if runtime.plan else ""
    results: list[dict[str, Any]] = []
    for st in ready:
        prompt = _build_auto_prompt({"name": st.name, "description": st.prompt}, goal)
        try:
            result = await dispatch_agent.handler(
                {"task_name": st.name, "prompt": prompt, "agent_type": st.agent_type}
            )
            content = result.get("content", [{}])
            text = content[0].get("text", "")[:200] if content else ""
            results.append({"task_name": st.name, "dispatched": True, "summary": text})
            console.print(
                f"  [bold cyan]{P}[/bold cyan]  dep-ready: dispatched [bold]{st.name}[/bold]"
            )
        except Exception:
            tb = traceback.format_exc()
            console.print(f"  [yellow]![/yellow]  Dep-dispatch {st.name} failed:\n{tb}")
            results.append({"task_name": st.name, "dispatched": False, "error": tb})
    return results


def _build_auto_prompt(subtask: dict[str, Any], goal: str) -> str:
    """Build a dispatch prompt from plan data without LLM generation.

    Omits the full goal — subtask descriptions are written as complete briefs
    by the lead agent, so repeating the goal wastes tokens and slows inference.
    """
    files = subtask.get("files", [])
    files_block = "\n".join(f"- {f}" for f in files) if files else ""
    parts = [subtask["description"]]
    if files_block:
        parts.append(f"## Files\n{files_block}")
    parts.append(
        "Write all code, run tests to verify, and commit your changes. "
        "Be concise — minimize tool calls and do not over-engineer."
    )
    return "\n\n".join(parts)


async def _auto_dispatch_from_plan(
    runtime: Any,
    subtasks: list[dict[str, Any]],
    parallel_groups: list[list[str]],
) -> list[dict[str, Any]]:
    """Auto-dispatch root subtasks immediately after plan acceptance."""
    from openmax.lead_agent.tools._dispatch import dispatch_agent

    roots = _roots_from_plan(subtasks, parallel_groups)
    if not roots:
        return []

    goal = getattr(runtime.plan, "goal", "") if runtime.plan else ""
    results: list[dict[str, Any]] = [{}] * len(roots)

    async def _dispatch_one(idx: int, st: dict[str, Any]) -> None:
        prompt = st.get("prompt") or _build_auto_prompt(st, goal)
        agent_type = st.get("agent_type", "claude-code")
        try:
            result = await dispatch_agent.handler(
                {"task_name": st["name"], "prompt": prompt, "agent_type": agent_type}
            )
            content = result.get("content", [{}])
            text = content[0].get("text", "")[:200] if content else ""
            results[idx] = {"task_name": st["name"], "dispatched": True, "summary": text}
        except Exception:
            tb = traceback.format_exc()
            console.print(f"  [yellow]![/yellow]  Auto-dispatch {st['name']} failed:\n{tb}")
            results[idx] = {"task_name": st["name"], "dispatched": False, "error": tb}

    async with anyio.create_task_group() as tg:
        for i, st in enumerate(roots):
            tg.start_soon(_dispatch_one, i, st)
    return results


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


def _get_shared_dirs(files_a: list[str], files_b: list[str]) -> set[str]:
    """Return directory paths shared between two file lists."""
    dirs_a = {str(PurePosixPath(f).parent) for f in files_a}
    dirs_b = {str(PurePosixPath(f).parent) for f in files_b}
    return dirs_a & dirs_b


def predict_conflicts(
    subtasks: list[dict[str, Any]],
    parallel_groups: list[list[str]],
    conflict_rates: dict[str, float],
    threshold: float = 0.5,
) -> list[str]:
    """Return warnings about likely merge conflicts in parallel groups."""
    task_by_name = {t["name"]: t for t in subtasks}
    warnings: list[str] = []
    for group in parallel_groups:
        group_tasks = [task_by_name[n] for n in group if n in task_by_name]
        for i, t1 in enumerate(group_tasks):
            for t2 in group_tasks[i + 1 :]:
                shared = _get_shared_dirs(t1.get("files", []), t2.get("files", []))
                for d in sorted(shared):
                    rate = conflict_rates.get(d, 0.0)
                    if rate > threshold:
                        warnings.append(
                            f"{t1['name']} and {t2['name']} share dir "
                            f"'{d}' (conflict rate: {rate:.0%}). "
                            f"Consider serializing."
                        )
    return warnings


def _format_plan_for_display(
    subtasks: list[dict[str, Any]],
    rationale: str,
    parallel_groups: list[list[str]],
) -> None:
    console.print("\n  [bold cyan]Proposed Plan[/bold cyan]")
    console.print(f"  [dim]{rationale}[/dim]\n")
    for i, st in enumerate(subtasks, 1):
        files_str = ", ".join(st.get("files", []))[:60]
        agent_tag = f" [dim]({st['agent_type']})[/dim]" if st.get("agent_type") else ""
        console.print(f"  {i}. [bold]{st['name']}[/bold]{agent_tag} — {st['description']}")
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
    coordinator = runtime.ui_coordinator

    def _do_prompt() -> str:
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
        return raw

    task_label = getattr(runtime.plan, "goal", "task")[:60]
    raw = coordinator.request_input(task_label, _do_prompt) if coordinator else _do_prompt()
    if raw.lower() in ("", "y", "yes"):
        return None
    return raw


@tool(
    "submit_plan",
    "Submit task decomposition plan. Validates deps and parallel groups. Call before dispatch.",
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
                        "agent_type": {"type": "string"},
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

    stats = load_stats(runtime.cwd)
    conflict_warnings = predict_conflicts(
        subtasks_raw, parallel_groups, stats.merge_conflict_rate_by_dir
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
    if conflict_warnings:
        result_data["conflict_warnings"] = conflict_warnings
        for warning in conflict_warnings:
            console.print(f"  [yellow]⚠[/yellow]  {warning}")

    # Harness mode: planner → generator ↔ evaluator loop per subtask
    if runtime.harness_mode and runtime.mailbox is not None:
        from openmax.quality_workflow import run_harness_workflow

        n = len(subtasks_raw)
        console.print(f"  [bold yellow]H[/bold yellow]  harness workflow: {n} tasks")
        all_results: list[dict[str, Any]] = []
        for st in subtasks_raw:
            prompt = st.get("prompt") or st["description"]
            wf_results = await run_harness_workflow(runtime, st["name"], prompt)
            all_results.extend(wf_results)

        from openmax.lead_agent.tools._misc import _run_all_done_pipeline

        pipeline = await _run_all_done_pipeline(runtime)
        result_data["status"] = "completed"
        result_data["harness_workflow"] = all_results
        result_data.update(pipeline)
        result_data["instruction"] = (
            "Harness workflow complete (plan → generate ↔ evaluate). Session done."
        )
        return _tool_response(result_data)

    # Quality mode: run write → review → rewrite workflow per subtask
    if runtime.quality_mode and runtime.mailbox is not None:
        from openmax.quality_workflow import run_quality_workflow

        n = len(subtasks_raw)
        console.print(f"  [bold magenta]Q[/bold magenta]  quality workflow: {n} tasks")
        all_results: list[dict[str, Any]] = []
        for st in subtasks_raw:
            prompt = st.get("prompt") or st["description"]
            wf_results = await run_quality_workflow(runtime, st["name"], prompt)
            all_results.extend(wf_results)

        from openmax.lead_agent.tools._misc import _run_all_done_pipeline

        pipeline = await _run_all_done_pipeline(runtime)
        result_data["status"] = "completed"
        result_data["quality_workflow"] = all_results
        result_data.update(pipeline)
        result_data["instruction"] = (
            "Quality workflow complete (write → review → challenge → rewrite). Session done."
        )
        return _tool_response(result_data)

    _register_all_subtasks(runtime, subtasks_raw)

    dispatched = await _auto_dispatch_from_plan(runtime, subtasks_raw, parallel_groups)
    ok = [d for d in dispatched if d.get("dispatched")]
    if not ok:
        return _tool_response(result_data)

    result_data["auto_dispatched"] = dispatched
    all_dispatched = len(ok) == len(subtasks_raw)

    # Inline monitoring: when ALL tasks dispatched (no deps), block until done
    if all_dispatched and runtime.mailbox is not None:
        from openmax.lead_agent.tools._misc import _monitor_until_done, _run_all_done_pipeline

        console.print(f"  [bold cyan]{P}[/bold cyan]  monitoring {len(ok)} agents inline...")
        results, all_done = await _monitor_until_done(runtime, timeout=600)
        if all_done:
            pipeline = await _run_all_done_pipeline(runtime)
            result_data["status"] = "completed"
            result_data["monitoring"] = results
            result_data.update(pipeline)
            result_data["instruction"] = (
                "All tasks completed, merged, verified, and reported. "
                "Session is DONE. Respond with a brief summary and stop. "
                "Do NOT call any more tools — everything is already handled."
            )
            return _tool_response(result_data)
        result_data["monitoring"] = results

    result_data["status"] = "accepted_and_dispatched"
    names = [d["task_name"] for d in ok]
    result_data["instruction"] = (
        f"All {len(ok)} root subtasks ({', '.join(names)}) are already dispatched. "
        "Do NOT call dispatch_agent for these tasks. "
        "Proceed directly to monitoring with wait_for_agent_message(timeout=60)."
    )
    return _tool_response(result_data)


@tool(
    "transition_phase",
    "Move between workflow phases (research→plan→implement→verify). "
    "Requires gate_summary ≥20 chars.",
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
    "Check for pending agent decision forks. Include in every monitoring round.",
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
    "Resolve a pending checkpoint. Records decision on blackboard and sends to agent.",
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
    "Mark a sub-task as completed. Call after verifying agent committed.",
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
            _append_session_event(
                "subtask.usage",
                {
                    "task_name": task_name,
                    "agent_type": st.agent_type,
                    "input_tokens": st.input_tokens,
                    "output_tokens": st.output_tokens,
                    "cost_usd": st.cost_usd,
                    "usage_source": st.usage_source,
                },
            )
            # Auto-dispatch queued tasks now that a slot opened
            from openmax.lead_agent.tools._dispatch import drain_dispatch_queue

            dequeued = await drain_dispatch_queue(runtime)

            msg = f"Marked '{task_name}' as done"
            if st.branch_name:
                msg += f". Branch '{st.branch_name}' ready — call merge_agent_branch to merge."
            if dequeued:
                names = [d["task_name"] for d in dequeued]
                msg += f" Auto-dispatched queued: {', '.join(names)}."
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
