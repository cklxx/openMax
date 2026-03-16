"""All @tool functions for the lead agent MCP server."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
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


@tool(
    "submit_plan",
    "Submit a structured task decomposition before dispatching "
    "agents. Validates dependencies (no cycles) and parallel "
    "groups (no conflicts). Call this after planning and before "
    "any dispatch_agent calls.",
    {
        "subtasks": list,
        "rationale": str,
        "parallel_groups": list,
    },
)
async def submit_plan(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    subtasks_raw = args.get("subtasks", [])
    rationale = args.get("rationale", "")
    parallel_groups = args.get("parallel_groups", [])

    if not subtasks_raw:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": "No subtasks provided"}),
                }
            ]
        }

    # Validate: no circular dependencies
    cycle_err = _topological_sort_check(subtasks_raw)
    if cycle_err:
        return {"content": [{"type": "text", "text": json.dumps({"error": cycle_err})}]}

    # Validate: parallel group members exist
    all_names = {st["name"] for st in subtasks_raw}
    for group in parallel_groups:
        for name in group:
            if name not in all_names:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": (f"Parallel group member '{name}' not in subtasks")}
                            ),
                        }
                    ]
                }

    # Validate: no dependency conflicts within parallel groups
    dep_map = {st["name"]: set(st.get("dependencies", [])) for st in subtasks_raw}
    for group in parallel_groups:
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if a in dep_map.get(b, set()) or b in dep_map.get(a, set()):
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "error": (
                                            f"Parallel group conflict: "
                                            f"'{a}' and '{b}' have a "
                                            "dependency relationship"
                                        )
                                    }
                                ),
                            }
                        ]
                    }

    # Store the plan submission
    runtime.plan_submitted = True

    plan_data = {
        "subtasks": subtasks_raw,
        "rationale": rationale,
        "parallel_groups": parallel_groups,
    }

    console.print(
        f"  [green]\u2713[/green] Plan submitted: "
        f"{len(subtasks_raw)} subtasks, "
        f"{len(parallel_groups)} parallel groups"
    )
    _append_session_event("tool.submit_plan", plan_data)

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "accepted",
                        "subtask_count": len(subtasks_raw),
                        "parallel_group_count": len(parallel_groups),
                    }
                ),
            }
        ]
    }


def _try_reuse_done_pane(
    runtime: LeadAgentRuntime, agent_type: str, task_name: str
) -> SimpleNamespace | None:
    """Find a done pane with the same agent type to reuse."""
    for st in runtime.plan.subtasks:
        if (
            st.agent_type == agent_type
            and st.status == TaskStatus.DONE
            and st.pane_id is not None
            and runtime.pane_mgr.is_pane_alive(st.pane_id)
        ):
            console.print(
                f"  [cyan]\u21bb[/cyan] Reusing pane {st.pane_id} (was {st.name}) for {task_name}"
            )
            return SimpleNamespace(
                pane_id=st.pane_id,
                window_id=runtime.agent_window_id,
            )
    return None


async def _wait_and_send_prompt(
    runtime: LeadAgentRuntime,
    pane: SimpleNamespace,
    cmd_spec: Any,
    agent_type: str,
) -> None:
    """Wait for CLI ready then send the initial prompt."""
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
        "override_reason": str,
    },
)
async def dispatch_agent(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    agent_type = args.get("agent_type", "claude-code")
    prompt = args["prompt"]

    # Auto-derive recommended agent from memory rankings
    recommended_agent = None
    if runtime.memory_store is not None:
        try:
            rankings = runtime.memory_store.derive_agent_rankings(cwd=runtime.cwd, task=task_name)
            if rankings:
                recommended_agent = rankings[0].agent_type
        except Exception:
            pass

    # Soft check: warn if submit_plan hasn't been called
    if not runtime.plan_submitted:
        console.print(
            "  [yellow]\u26a0[/yellow] dispatch_agent called before "
            "submit_plan — consider submitting a structured plan first"
        )

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

    # Auto-inject workspace memory context
    if runtime.memory_store is not None:
        try:
            memory_context = runtime.memory_store.build_context(
                cwd=runtime.cwd,
                task=task_name,
            )
            if memory_context and memory_context.text:
                prompt = prompt + "\n\n## Workspace Context\n" + memory_context.text
        except Exception:
            pass  # Don't fail dispatch if memory lookup fails

    cmd_spec = adapter.get_command(prompt, cwd=runtime.cwd)
    launch_env = cmd_spec.env or None

    # ── Try to reuse a done pane with the same agent type ────────
    reused_pane = _try_reuse_done_pane(runtime, agent_type, task_name)

    if reused_pane is not None:
        pane = reused_pane
        # Reset context: send /clear, wait briefly, then send new prompt
        runtime.pane_mgr.send_text(pane.pane_id, "/clear")
        await anyio.sleep(1.0)
        if cmd_spec.interactive and cmd_spec.initial_input:
            runtime.pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)
    elif runtime.agent_window_id is None:
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

        # Wait for CLI ready then send initial prompt
        if cmd_spec.interactive and cmd_spec.initial_input:
            await _wait_and_send_prompt(
                runtime,
                pane,
                cmd_spec,
                agent_type,
            )
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

        # Wait for CLI ready then send initial prompt
        if cmd_spec.interactive and cmd_spec.initial_input:
            await _wait_and_send_prompt(
                runtime,
                pane,
                cmd_spec,
                agent_type,
            )

    subtask = SubTask(
        name=task_name,
        agent_type=agent_type,
        prompt=prompt,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
        started_at=time.time(),
    )
    _upsert_subtask(subtask)
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(
            task_name,
            agent_type,
            pane.pane_id,
            "running",
            started_at=subtask.started_at,
        )

    # Show layout info
    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [green]\u2713[/green] Dispatched [bold]{task_name}[/bold] "
        f"\u2192 pane {pane.pane_id} ({agent_type}) "
        f"[dim][window {runtime.agent_window_id}, {pane_count} panes][/dim]"
    )
    event_payload = {
        "task_name": task_name,
        "agent_type": agent_type,
        "prompt": prompt,
        "pane_id": pane.pane_id,
        "window_id": runtime.agent_window_id,
        "panes_in_window": pane_count,
        "recommended_agent": recommended_agent,
    }
    override_reason = args.get("override_reason")
    if override_reason:
        event_payload["override_reason"] = override_reason
    _append_session_event("tool.dispatch_agent", event_payload)

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


_STUCK_THRESHOLD = 3  # consecutive identical outputs to trigger stuck


@tool(
    "read_pane_output",
    "Read the current terminal output of an agent pane (~100 tail lines). "
    "Error lines from earlier output appear at the top with [ERROR] prefix. "
    "Returns JSON with 'text' and 'stuck' fields. "
    "stuck=true when output is unchanged for 3 consecutive reads (~60-90s).",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    try:
        text = runtime.pane_mgr.get_text(pane_id)
        text = _extract_smart_output(text, tail_lines=100)

        # Track output hashes for stuck detection
        output_hash = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
        hash_history = runtime.pane_output_hashes.setdefault(pane_id, [])
        hash_history.append(output_hash)
        # Keep only the last _STUCK_THRESHOLD entries
        if len(hash_history) > _STUCK_THRESHOLD:
            runtime.pane_output_hashes[pane_id] = hash_history[-_STUCK_THRESHOLD:]
            hash_history = runtime.pane_output_hashes[pane_id]

        stuck = (
            len(hash_history) >= _STUCK_THRESHOLD
            and len(set(hash_history[-_STUCK_THRESHOLD:])) == 1
        )

        # Check if pane exited
        pane_alive = runtime.pane_mgr.is_pane_alive(pane_id)
        exited = not pane_alive

        result = json.dumps({"text": text, "stuck": stuck, "exited": exited})
        _append_session_event(
            "tool.read_pane_output",
            {
                "pane_id": pane_id,
                "preview": text[:500],
                "stuck": stuck,
                "exited": exited,
            },
        )
        return {"content": [{"type": "text", "text": result}]}
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
            if notes:
                st.completion_notes = notes
            console.print(f"  [green]\u2713\u2713[/green] [bold]{task_name}[/bold] done")
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
    "transition_phase",
    "Transition between workflow phases. Validates from_phase matches current, "
    "gate_summary is descriptive. Records phase.anchor event and updates session phase.",
    {
        "from_phase": str,
        "to_phase": str,
        "gate_summary": str,
        "artifacts": list,
    },
)
async def transition_phase(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    from_phase = args.get("from_phase", "").strip().lower()
    to_phase = args.get("to_phase", "").strip().lower()
    gate_summary = args.get("gate_summary", "").strip()
    artifacts = args.get("artifacts", [])

    # Validate gate_summary length
    if len(gate_summary) < 20:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Error: gate_summary must be at least 20 characters "
                        f"(got {len(gate_summary)})"
                    ),
                }
            ]
        }

    # Validate from_phase matches current (if session has a phase)
    current_phase = None
    if runtime.session_meta:
        current_phase = (runtime.session_meta.latest_phase or "").strip().lower()
    if current_phase and from_phase != current_phase:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Error: from_phase '{from_phase}' does not match "
                        f"current phase '{current_phase}'"
                    ),
                }
            ]
        }

    # Record phase anchor and update session
    _record_phase_anchor(to_phase, gate_summary)

    # Update dashboard if available
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

    return {
        "content": [
            {
                "type": "text",
                "text": f"Transitioned from '{from_phase}' to '{to_phase}'",
            }
        ]
    }


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
    "run_verification",
    "Run a verification command (lint, test, build) and return structured pass/fail. "
    "Executes the command in a temporary pane, polls for completion, and returns "
    "{status, exit_code, output, duration_s}. Use after all agents finish.",
    {
        "check_type": str,
        "command": str,
        "timeout": int,
    },
)
async def run_verification(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    check_type = args.get("check_type", "custom")
    command_str = args["command"]
    timeout = min(max(args.get("timeout", 120), 10), 600)

    import shlex

    try:
        cmd_list = shlex.split(command_str)
    except ValueError:
        cmd_list = [command_str]
    if not cmd_list:
        return {"content": [{"type": "text", "text": "Error: empty command"}]}

    # Wrap command to capture exit code: run cmd; echo __EXIT_$?__
    wrapped_cmd = f'{command_str}; echo "__OPENMAX_EXIT_$?__"'
    shell_cmd = ["bash", "-c", wrapped_cmd]

    task_name = f"verify-{check_type}"
    if runtime.agent_window_id is None:
        pane = runtime.pane_mgr.create_window(
            command=shell_cmd,
            purpose=task_name,
            agent_type="command",
            title=f"openMax: verify {check_type}",
            cwd=runtime.cwd,
        )
        runtime.agent_window_id = pane.window_id
    else:
        pane = runtime.pane_mgr.add_pane(
            window_id=runtime.agent_window_id,
            command=shell_cmd,
            purpose=task_name,
            agent_type="command",
            cwd=runtime.cwd,
        )

    console.print(
        f"  [blue]\u2713[/blue] Verifying [{check_type}]: {command_str[:60]} "
        f"\u2192 pane {pane.pane_id}"
    )

    # Poll for the exit marker
    start_ts = time.monotonic()
    deadline = start_ts + timeout
    exit_code: int | None = None
    output = ""

    while time.monotonic() < deadline:
        await anyio.sleep(2)
        try:
            text = runtime.pane_mgr.get_text(pane.pane_id)
        except Exception:
            text = ""
        # Look for our exit marker
        import re

        match = re.search(r"__OPENMAX_EXIT_(\d+)__", text)
        if match:
            exit_code = int(match.group(1))
            # Remove the marker from output
            output = text[: match.start()].strip()
            break

    duration_s = int(time.monotonic() - start_ts)

    if exit_code is None:
        status = "timeout"
        output = _extract_smart_output(text, tail_lines=50) if text else ""
    elif exit_code == 0:
        status = "pass"
    else:
        status = "fail"

    if not output and text:
        output = _extract_smart_output(text, tail_lines=50)

    result = {
        "status": status,
        "check_type": check_type,
        "exit_code": exit_code,
        "output": output[-2000:],  # cap output size
        "duration_s": duration_s,
        "command": command_str,
    }

    style = "green" if status == "pass" else "red" if status == "fail" else "yellow"
    console.print(
        f"  [{style}]\u2713[/{style}] Verification [{check_type}]: "
        f"{status} (exit={exit_code}, {duration_s}s)"
    )

    _append_session_event("tool.run_verification", result)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool(
    "check_conflicts",
    "Check for git conflicts and untracked files in the working directory.",
    {},
)
async def check_conflicts(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    cwd = runtime.cwd

    # Run git status --porcelain
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        status_output = status_result.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "conflict": False,
                            "details": f"Error running git status: {e}",
                            "untracked_files": [],
                        }
                    ),
                }
            ]
        }

    # Run git diff --check
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--check"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        diff_check_failed = diff_result.returncode != 0
        diff_output = diff_result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        diff_check_failed = False
        diff_output = ""

    # Parse untracked files and conflict markers
    untracked_files: list[str] = []
    conflict_markers = False
    for line in status_output.splitlines():
        if line.startswith("??"):
            untracked_files.append(line[3:].strip())
        elif line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
            conflict_markers = True

    has_conflict = diff_check_failed or conflict_markers
    if has_conflict:
        details = diff_output if diff_output else "Conflict markers detected in git status"
    else:
        details = "No conflicts detected"

    result = {
        "conflict": has_conflict,
        "details": details,
        "untracked_files": untracked_files,
    }

    console.print(
        f"  [{'red' if has_conflict else 'green'}]"
        f"{'⚠' if has_conflict else '✓'}[/{'red' if has_conflict else 'green'}] "
        f"Conflict check: {details[:80]}"
    )
    _append_session_event("tool.check_conflicts", result)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


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
    check_conflicts,
    dispatch_agent,
    get_agent_recommendations,
    read_file_tool,
    read_pane_output,
    run_command,
    run_verification,
    send_text_to_pane,
    submit_plan,
    list_managed_panes,
    mark_task_done,
    record_phase_anchor,
    remember_learning,
    report_completion,
    transition_phase,
    wait_tool,
]
