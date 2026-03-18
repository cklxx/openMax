"""Tools for dispatching and interacting with agents."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import anyio
from claude_agent_sdk import tool

from openmax.lead_agent.tools._costing import estimate_task_cost
from openmax.lead_agent.tools._helpers import (
    _CHECKPOINT_PROTOCOL,
    _append_session_event,
    _build_blackboard_block,
    _build_role_context,
    _build_subagent_context,
    _compress_context,
    _extract_smart_output,
    _file_protocol_section,
    _read_subtask_report_for_pane,
    _runtime,
    _safe_launch_pane,
    _tool_response,
    _try_reuse_done_pane,
    _upsert_subtask,
    _wait_and_send_prompt,
    strip_terminal_noise,
)
from openmax.lead_agent.tools._verify import (
    _create_agent_branch,
    _get_integration_branch,
    _sanitize_branch_name,
)
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.output import P, console
from openmax.task_file import inject_claude_md, report_path, write_brief

_STUCK_THRESHOLD = 3  # consecutive identical outputs to trigger stuck
_MAX_RETRIES = 2


def _check_budget_warning(task_name: str, used: int, budget: int) -> str | None:
    """Check token budget and return warning level if threshold exceeded."""
    if used >= budget:
        return "hard_limit"
    if used >= 0.8 * budget:
        return "soft_limit"
    return None


def _get_retry_info_for_pane(pane_id: int) -> dict[str, Any]:
    """Look up retry_count and max_retries for the subtask owning this pane."""
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.pane_id == pane_id:
            return {
                "retry_count": st.retry_count,
                "max_retries": st.max_retries,
                "task_name": st.name,
                "can_retry": st.retry_count < st.max_retries,
            }
    return {}


def _resolve_agent_type(runtime: Any, agent_type: str) -> str:
    """Enforce allowed agents constraint and fall back to defaults."""
    if runtime.allowed_agents and agent_type not in runtime.allowed_agents:
        fallback = runtime.allowed_agents[0]
        console.print(
            f"  [yellow]![/yellow]Agent '{agent_type}' not allowed, using '{fallback}' instead"
        )
        return fallback
    return agent_type


def _resolve_adapter(runtime: Any, agent_type: str) -> tuple[str, Any]:
    """Get adapter for agent_type, falling back to default. Returns (agent_type, adapter)."""
    adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        fallback = runtime.agent_registry.default_agent_name()
        if fallback is None:
            raise RuntimeError("No agents are configured")
        console.print(
            f"  [yellow]![/yellow]Agent '{agent_type}' not configured, using '{fallback}' instead"
        )
        agent_type = fallback
        adapter = runtime.agent_registry.get(agent_type)
    if adapter is None:
        raise RuntimeError(f"Agent '{agent_type}' is unavailable")
    return agent_type, adapter


def _deduplicate_task_name(runtime: Any, task_name: str, is_retry: bool) -> str:
    """Return a unique task name, suffixing if needed."""
    existing_names = {st.name for st in runtime.plan.subtasks}
    if task_name not in existing_names or is_retry:
        return task_name
    suffix = 2
    while f"{task_name}-{suffix}" in existing_names:
        suffix += 1
    new_name = f"{task_name}-{suffix}"
    console.print(f"  [yellow]![/yellow]Task '{task_name}' already exists, renamed to '{new_name}'")
    return new_name


def _gather_memory_text(runtime: Any, task_name: str, context_budget: int) -> str | None:
    """Fetch and compress memory context for a task."""
    if runtime.memory_store is None:
        return None
    try:
        memory_context = runtime.memory_store.build_context(cwd=runtime.cwd, task=task_name)
        if memory_context and memory_context.text:
            return _compress_context(memory_context.text, context_budget)
    except Exception:
        pass
    return None


def _setup_branch_isolation(runtime: Any, task_name: str) -> tuple[str | None, str]:
    """Create per-agent branch + worktree. Returns (branch_name, agent_cwd)."""
    if runtime.integration_branch is None:
        runtime.integration_branch = _get_integration_branch(runtime.cwd)

    branch_name_candidate = _sanitize_branch_name(task_name)
    worktree_path, branch_err = _create_agent_branch(runtime.cwd, branch_name_candidate)
    if worktree_path is not None:
        inject_claude_md(worktree_path, task_name)
        console.print(f"  [dim]{P}  branch {branch_name_candidate} → {worktree_path}[/dim]")
        return branch_name_candidate, worktree_path
    if branch_err:
        console.print(f"  [yellow]![/yellow]  Branch isolation skipped: {branch_err}")
    return None, runtime.cwd


def _build_full_prompt(
    prompt: str,
    branch_name: str | None,
    agent_cwd: str,
    memory_text: str | None,
    task_name: str,
    brief_file: Any,
    rep_file: Any,
    role_context: str = "",
) -> str:
    context_block = _build_subagent_context(
        branch_name=branch_name, agent_cwd=agent_cwd, memory_text=memory_text
    )
    if context_block:
        prompt = prompt + context_block
    blackboard_block = _build_blackboard_block(agent_cwd)
    if blackboard_block:
        prompt = prompt + blackboard_block
    if role_context:
        prompt = prompt + "\n\n" + role_context
    prompt = prompt + _CHECKPOINT_PROTOCOL.format(task_name=task_name)
    return prompt + _file_protocol_section(brief_file, rep_file, agent_cwd)


def _dispatch_failure_response(
    task_name: str, agent_type: str, branch_name: str | None, launch_err: str | None
) -> dict[str, Any]:
    console.print(f"  [bold red]✗[/bold red]  dispatch failed: {launch_err}")
    _append_session_event(
        "tool.dispatch_agent.failed",
        {
            "task_name": task_name,
            "agent_type": agent_type,
            "error": launch_err,
            "branch_name": branch_name,
        },
    )
    return _tool_response(
        {
            "status": "error",
            "error": launch_err,
            "task_name": task_name,
            "agent_type": agent_type,
            "remediation": (
                "Check that the terminal backend (kaku/tmux) is running. "
                "Run 'openmax doctor' for diagnostics."
            ),
        }
    )


@tool(
    "dispatch_agent",
    "Dispatch a sub-task to an AI agent in a terminal pane. "
    "The prompt is the agent's ONLY context \u2014 include file paths, constraints, "
    "and any knowledge it cannot discover on its own. "
    "All agents share one window with smart grid layout. Returns pane_id.",
    {
        "type": "object",
        "properties": {
            "task_name": {"type": "string"},
            "agent_type": {"type": "string"},
            "prompt": {"type": "string"},
            "override_reason": {"type": "string"},
            "retry_count": {"type": "integer"},
            "context_budget_tokens": {"type": "integer"},
            "token_budget": {"type": "integer"},
            "role": {
                "type": "string",
                "enum": ["writer", "reviewer", "challenger", "debugger"],
            },
        },
        "required": ["task_name", "agent_type", "prompt"],
    },
)
async def dispatch_agent(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    agent_type = args.get("agent_type", "claude-code")
    prompt = args["prompt"]
    retry_count = args.get("retry_count", 0)
    token_budget = args.get("token_budget")
    role = args.get("role", "writer")
    if role not in ("writer", "reviewer", "challenger", "debugger"):
        role = "writer"
    if not isinstance(retry_count, int) or retry_count < 0:
        retry_count = 0

    existing_names = {st.name for st in runtime.plan.subtasks}
    is_retry = retry_count > 0 and task_name in existing_names
    task_name = _deduplicate_task_name(runtime, task_name, is_retry)

    recommended_agent = None
    if runtime.memory_store is not None:
        try:
            rankings = runtime.memory_store.derive_agent_rankings(cwd=runtime.cwd, task=task_name)
            if rankings:
                recommended_agent = rankings[0].agent_type
        except Exception:
            pass

    agent_type = _resolve_agent_type(runtime, agent_type)
    agent_type, adapter = _resolve_adapter(runtime, agent_type)

    context_budget = args.get("context_budget_tokens", 2000)
    if not isinstance(context_budget, int) or context_budget < 0:
        context_budget = 2000
    memory_text = _gather_memory_text(runtime, task_name, context_budget)

    branch_name, agent_cwd = _setup_branch_isolation(runtime, task_name)

    brief_file = write_brief(agent_cwd, task_name, prompt)
    if agent_cwd != runtime.cwd:
        write_brief(runtime.cwd, task_name, prompt)
    rep_file = report_path(agent_cwd, task_name)

    role_context = _build_role_context(role)
    prompt = _build_full_prompt(
        prompt,
        branch_name,
        agent_cwd,
        memory_text,
        task_name,
        brief_file,
        rep_file,
        role_context=role_context,
    )

    cost_estimate = estimate_task_cost(len(prompt), agent_type)

    cmd_spec = adapter.get_command(prompt, cwd=agent_cwd)
    launch_env = cmd_spec.env or None

    has_worktree = agent_cwd != runtime.cwd
    reused_pane = None if has_worktree else _try_reuse_done_pane(runtime, agent_type, task_name)

    ready_confirmed = True
    if reused_pane is not None:
        pane = reused_pane
        runtime.pane_mgr.send_text(pane.pane_id, "/clear")
        await anyio.sleep(1.0)
        if cmd_spec.interactive and cmd_spec.initial_input:
            runtime.pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)
    else:
        pane, launch_err = _safe_launch_pane(
            runtime,
            command=cmd_spec.launch_cmd,
            purpose=task_name,
            agent_type=agent_type,
            title=f"openMax: {runtime.plan.goal[:40]}",
            cwd=agent_cwd,
            env=launch_env,
        )
        if pane is None:
            return _dispatch_failure_response(task_name, agent_type, branch_name, launch_err)
        if cmd_spec.interactive and cmd_spec.initial_input:
            ready_confirmed = await _wait_and_send_prompt(runtime, pane, cmd_spec, agent_type)

    subtask = SubTask(
        name=task_name,
        agent_type=agent_type,
        prompt=prompt,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
        retry_count=retry_count,
        branch_name=branch_name,
        started_at=time.time(),
        token_budget=token_budget,
        role=role,
        estimated_cost_usd=cost_estimate.estimated_cost_usd,
    )
    _upsert_subtask(subtask)
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(
            task_name, agent_type, pane.pane_id, "running", started_at=subtask.started_at
        )

    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [bold cyan]{P}[/bold cyan]  [bold]{task_name}[/bold]"
        f" \u2192 pane {pane.pane_id} [dim]({agent_type})[/dim]"
    )
    if retry_count > 0:
        console.print(
            f"  [yellow]\u21bb[/yellow]  Retrying '{task_name}' (attempt {retry_count + 1})"
        )

    event_payload: dict[str, Any] = {
        "task_name": task_name,
        "agent_type": agent_type,
        "prompt": prompt,
        "pane_id": pane.pane_id,
        "window_id": runtime.agent_window_id,
        "panes_in_window": pane_count,
        "recommended_agent": recommended_agent,
        "retry_count": retry_count,
        "branch_name": branch_name,
        "role": role,
        "estimated_cost_usd": cost_estimate.estimated_cost_usd,
        "estimated_tokens": cost_estimate.estimated_tokens,
    }
    override_reason = args.get("override_reason")
    if override_reason:
        event_payload["override_reason"] = override_reason
    _append_session_event("tool.dispatch_agent", event_payload)

    response_payload: dict[str, Any] = {
        "status": "dispatched",
        "pane_id": pane.pane_id,
        "window_id": runtime.agent_window_id,
        "agent_type": agent_type,
        "task_name": task_name,
        "panes_in_window": pane_count,
        "retry_count": retry_count,
        "branch_name": branch_name,
        "token_budget": token_budget,
        "role": role,
        "estimated_cost_usd": cost_estimate.estimated_cost_usd,
        "estimated_tokens": cost_estimate.estimated_tokens,
    }
    if not ready_confirmed:
        response_payload["ready_timeout"] = True
    return _tool_response(response_payload)


@tool(
    "read_pane_output",
    "Read agent pane output. If pane_id is given, returns ~100 tail lines with "
    "stuck/exited detection. If pane_id is omitted or -1, lists all managed panes "
    "and their states (replaces list_managed_panes).",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args.get("pane_id", -1)

    if pane_id == -1:
        runtime.pane_mgr.refresh_states()
        summary = runtime.pane_mgr.summary()
        return _tool_response(summary)
    try:
        pane_alive = runtime.pane_mgr.is_pane_alive(pane_id)
        try:
            text = runtime.pane_mgr.get_text(pane_id)
        except Exception:
            text = ""

        if not pane_alive:
            text = _extract_smart_output(text, tail_lines=100) if text else ""
            retry_info = _get_retry_info_for_pane(pane_id)
            response: dict[str, Any] = {
                "text": text or "(pane no longer exists)",
                "stuck": False,
                "exited": True,
                "cached": True,
            }
            report = _read_subtask_report_for_pane(pane_id)
            if report:
                response["report"] = report[:4000]
            response.update(retry_info)
            event_payload: dict[str, Any] = {
                "pane_id": pane_id,
                "preview": text[:500],
                "stuck": False,
                "exited": True,
            }
            event_payload.update(retry_info)
            _append_session_event("tool.read_pane_output", event_payload)
            return _tool_response(response)

        text = _extract_smart_output(text, tail_lines=100)

        clean = strip_terminal_noise(text)
        output_hash = hashlib.md5(clean.encode(), usedforsecurity=False).hexdigest()
        hash_history = runtime.pane_output_hashes.setdefault(pane_id, [])
        hash_history.append(output_hash)
        if len(hash_history) > _STUCK_THRESHOLD:
            runtime.pane_output_hashes[pane_id] = hash_history[-_STUCK_THRESHOLD:]
            hash_history = runtime.pane_output_hashes[pane_id]

        stuck = (
            len(hash_history) >= _STUCK_THRESHOLD
            and len(set(hash_history[-_STUCK_THRESHOLD:])) == 1
        )
        exited = not runtime.pane_mgr.is_pane_alive(pane_id)

        response_data: dict[str, Any] = {"text": text, "stuck": stuck, "exited": exited}

        for st in runtime.plan.subtasks:
            if st.pane_id == pane_id and st.token_budget is not None:
                warning = _check_budget_warning(st.name, st.tokens_used, st.token_budget)
                budget_info: dict[str, Any] = {
                    "token_budget": st.token_budget,
                    "tokens_used": st.tokens_used,
                    "warning": warning,
                }
                if warning == "hard_limit":
                    budget_info["action"] = "stop_agent"
                response_data["budget"] = budget_info
                break

        _append_session_event(
            "tool.read_pane_output",
            {"pane_id": pane_id, "preview": text[:500], "stuck": stuck, "exited": exited},
        )
        return _tool_response(response_data)
    except RuntimeError as e:
        return _tool_response(f"Error: {e}")


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
    if not runtime.pane_mgr.is_pane_alive(pane_id):
        return _tool_response(
            f"Error: pane {pane_id} no longer exists. Re-dispatch the task to a new pane."
        )
    runtime.pane_mgr.send_text(pane_id, text)
    if runtime.dashboard is not None:
        runtime.dashboard.update_pane_activity(pane_id, text[:80])
    _append_session_event("tool.send_text_to_pane", {"pane_id": pane_id, "text": text})
    return _tool_response(f"Sent to pane {pane_id}")
