"""Tools for dispatching and interacting with agents."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import anyio
from claude_agent_sdk import tool

from openmax.lead_agent.tools._branch import (
    _create_agent_branch,
    _get_integration_branch,
    _sanitize_branch_name,
)
from openmax.lead_agent.tools._costing import estimate_task_cost
from openmax.lead_agent.tools._error_context import extract_error_context, is_rate_limit_error
from openmax.lead_agent.tools._helpers import (
    _CHECKPOINT_PROTOCOL,
    _append_session_event,
    _build_blackboard_block,
    _build_employee_context,
    _build_identity_block,
    _build_role_context,
    _build_subagent_context,
    _extract_smart_output,
    _file_protocol_section,
    _read_subtask_report_for_pane,
    _resolve_session_id,
    _runtime,
    _safe_launch_pane,
    _tool_response,
    _try_reuse_done_pane,
    _upsert_subtask,
    _wait_and_send_prompt,
    strip_terminal_noise,
)
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.output import P, console
from openmax.stats import SessionStats, clamp
from openmax.task_file import inject_claude_md, report_path, write_brief

_STUCK_BASE_THRESHOLD = 3
_MAX_RETRIES = 2
_RETRY_CONTEXT_MAX_CHARS = 2000


def _register_stream_callback(
    runtime: Any,
    pane_id: int,
    task_name: str,
    cwd: str,
) -> None:
    """Wire up stream-json callback: update dashboard + write log file."""
    from pathlib import Path

    from openmax.pane_backend import HeadlessPaneBackend

    backend = getattr(runtime.pane_mgr, "_backend", None)
    if not isinstance(backend, HeadlessPaneBackend):
        return
    log_dir = Path(cwd) / ".openmax" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_name}.jsonl"

    import json as _json

    def _on_event(pid: int, event: Any) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(event.raw, ensure_ascii=False) + "\n")
        if runtime.dashboard is None:
            return
        if event.type in ("tool_use", "text"):
            runtime.dashboard.update_pane_activity(pid, event.summary)
        if event.type == "tool_use":
            runtime.dashboard.add_tool_event(f"{task_name}: {event.summary}", "tool")

    backend.register_stream_callback(pane_id, _on_event)


def get_stuck_threshold(stats: SessionStats | None = None) -> int:
    """Adaptive stuck threshold based on historical false positive rate."""
    base = _STUCK_BASE_THRESHOLD
    if stats is None:
        return base
    rate = stats.stuck_false_positive_rate
    if rate > 0.5:
        threshold = 7
    elif rate > 0.3:
        threshold = 5
    else:
        threshold = base
    return int(clamp(threshold, 2, 10))


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


def _get_previous_pane_output(runtime: Any, task_name: str) -> str:
    """Get terminal output from the most recent subtask matching task_name."""
    for st in reversed(runtime.plan.subtasks):
        if st.name == task_name and st.pane_id is not None:
            try:
                return runtime.pane_mgr.get_text(st.pane_id)
            except Exception:
                return ""
    return ""


def _build_retry_prompt(original_prompt: str, error_context: str) -> str:
    """Prepend failure context to the retry prompt."""
    if not error_context:
        return original_prompt
    return (
        "[RETRY CONTEXT] Previous attempt failed. Error summary:\n"
        f"{error_context}\n\n"
        "Please try a different approach to avoid the same failure.\n"
        "---\n"
        f"{original_prompt}"
    )


_ROLE_TO_AGENT: dict[str, str] = {
    "reviewer": "claude-code",
    "challenger": "claude-code",
    "debugger": "claude-code",
    "writer": "codex",
}


def _auto_select_agent(runtime: Any, role: str) -> str:
    """Infer the best agent_type from role when both claude-code and codex are available."""
    allowed = runtime.allowed_agents or []
    has_both = "claude-code" in allowed and "codex" in allowed
    if has_both:
        return _ROLE_TO_AGENT.get(role, "codex")
    if allowed:
        return allowed[0]
    return runtime.agent_registry.default_agent_name() or "claude-code"


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


def _setup_branch_isolation_sync(cwd: str, task_name: str) -> tuple[str | None, str | None, str]:
    """Create per-agent branch + worktree (sync, for worker thread).

    Returns (branch_name, error, worktree_path_or_empty).
    """
    branch_name_candidate = _sanitize_branch_name(task_name)
    worktree_path, branch_err = _create_agent_branch(cwd, branch_name_candidate)
    if worktree_path is not None:
        return branch_name_candidate, None, worktree_path
    return None, branch_err, ""


async def _setup_branch_isolation(
    runtime: Any,
    task_name: str,
    session_id: str | None = None,
) -> tuple[str | None, str]:
    """Create per-agent branch + worktree. Returns (branch_name, agent_cwd)."""
    if runtime.integration_branch is None:
        runtime.integration_branch = await anyio.to_thread.run_sync(
            lambda: _get_integration_branch(runtime.cwd)
        )

    branch_name, err, wt_path = await anyio.to_thread.run_sync(
        lambda: _setup_branch_isolation_sync(runtime.cwd, task_name)
    )
    if branch_name and wt_path:
        inject_claude_md(wt_path, task_name, session_id=session_id)
        console.print(f"  [dim]{P}  branch {branch_name} → {wt_path}[/dim]")
        return branch_name, wt_path
    if err:
        console.print(f"  [yellow]![/yellow]  Branch isolation skipped: {err}")
    return None, runtime.cwd


def _get_archetype_hints(runtime: Any, task_name: str) -> str:
    """Get archetype-specific hints for a subtask, or empty string."""
    try:
        from openmax.archetypes import format_subtask_hints

        arch = getattr(runtime, "matched_archetype", None)
        return format_subtask_hints(arch) if arch else ""
    except Exception:
        return ""


def _build_full_prompt(
    prompt: str,
    branch_name: str | None,
    agent_cwd: str,
    task_name: str,
    rep_file: Any,
    role_context: str = "",
    session_id: str | None = None,
    archetype_hints: str = "",
    employee_context: str = "",
) -> str:
    identity = _build_identity_block(task_name, session_id)
    context_block = _build_subagent_context(branch_name=branch_name, agent_cwd=agent_cwd)
    blackboard_block = _build_blackboard_block(agent_cwd)
    parts = [identity, prompt]
    if context_block:
        parts.append(context_block)
    if blackboard_block:
        parts.append(blackboard_block)
    if archetype_hints:
        parts.append(archetype_hints)
    if role_context:
        parts.append("\n\n" + role_context)
    if employee_context:
        parts.append("\n\n" + employee_context)
    parts.append(_CHECKPOINT_PROTOCOL.format(task_name=task_name))
    parts.append(_file_protocol_section(rep_file, agent_cwd))
    return "".join(parts)


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
    "Dispatch a sub-task to an AI agent. Include file paths, constraints, "
    "and context in the prompt — it is the agent's only briefing. Returns pane_id. "
    "Use employee to assign a named employee profile (from list_employees).",
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
            "employee": {"type": "string"},
        },
        "required": ["task_name", "prompt"],
    },
)
async def dispatch_agent(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    prompt = args["prompt"]
    retry_count = args.get("retry_count", 0)
    token_budget = args.get("token_budget")
    role = args.get("role", "writer")
    if role not in ("writer", "reviewer", "challenger", "debugger"):
        role = "writer"
    agent_type = args.get("agent_type") or _auto_select_agent(runtime, role)
    if not isinstance(retry_count, int) or retry_count < 0:
        retry_count = 0

    existing_names = {st.name for st in runtime.plan.subtasks}
    is_retry = retry_count > 0 and task_name in existing_names

    if is_retry:
        prev_output = _get_previous_pane_output(runtime, task_name)
        error_ctx = extract_error_context(prev_output, max_chars=_RETRY_CONTEXT_MAX_CHARS)
        prompt = _build_retry_prompt(prompt, error_ctx)

    task_name = _deduplicate_task_name(runtime, task_name, is_retry)

    agent_type = _resolve_agent_type(runtime, agent_type)
    agent_type, adapter = _resolve_adapter(runtime, agent_type)

    session_id = _resolve_session_id()
    branch_name, agent_cwd = await _setup_branch_isolation(runtime, task_name, session_id)

    write_brief(agent_cwd, task_name, prompt)
    if agent_cwd != runtime.cwd:
        write_brief(runtime.cwd, task_name, prompt)
    rep_file = report_path(agent_cwd, task_name)

    role_context = _build_role_context(role)
    hints = _get_archetype_hints(runtime, task_name)
    employee_name = args.get("employee")
    employee_ctx = _build_employee_context(employee_name)
    prompt = _build_full_prompt(
        prompt,
        branch_name,
        agent_cwd,
        task_name,
        rep_file,
        role_context=role_context,
        session_id=session_id,
        archetype_hints=hints,
        employee_context=employee_ctx,
    )

    cost_estimate = estimate_task_cost(len(prompt), agent_type)

    cmd_spec = adapter.get_command(prompt, cwd=agent_cwd)
    session_env: dict[str, str] = {}
    if runtime.session_meta:
        session_env["OPENMAX_SESSION_ID"] = runtime.session_meta.session_id
    launch_env = {**(cmd_spec.env or {}), **session_env} or None

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
            stream_json=cmd_spec.stream_json,
        )
        if pane is None:
            return _dispatch_failure_response(task_name, agent_type, branch_name, launch_err)
        needs_prompt = cmd_spec.interactive and cmd_spec.initial_input
        has_trust = bool(getattr(cmd_spec, "trust_patterns", None))
        if needs_prompt or has_trust:
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
        employee=employee_name,
        estimated_cost_usd=cost_estimate.estimated_cost_usd,
    )
    _upsert_subtask(subtask)
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(
            task_name, agent_type, pane.pane_id, "running", started_at=subtask.started_at
        )
        runtime.dashboard.set_dispatch_prompt(task_name, args["prompt"])

    if cmd_spec.stream_json:
        _register_stream_callback(runtime, pane.pane_id, task_name, runtime.cwd)

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
    "Read agent output. With pane_id: ~100 tail lines + stuck/exited status. "
    "With pane_id=-1: lists all panes visible to the current backend and marks managed ones.",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args.get("pane_id", -1)

    if pane_id == -1:
        runtime.pane_mgr.refresh_states(force=True)
        if hasattr(runtime.pane_mgr, "all_panes_summary"):
            summary = runtime.pane_mgr.all_panes_summary()
        else:
            summary = runtime.pane_mgr.summary()
        return _tool_response(summary)
    try:
        pane_alive = runtime.pane_mgr.is_pane_alive(pane_id)
        try:
            text = runtime.pane_mgr.get_text(pane_id)
        except Exception:
            text = ""

        if not pane_alive:
            raw_text = text
            text = _extract_smart_output(text, tail_lines=100) if text else ""
            error_ctx = extract_error_context(raw_text) if raw_text else ""
            retry_info = _get_retry_info_for_pane(pane_id)
            rate_limited = is_rate_limit_error(raw_text) if raw_text else False
            response: dict[str, Any] = {
                "text": text or "(pane no longer exists)",
                "stuck": False,
                "exited": True,
                "cached": True,
            }
            if error_ctx:
                response["error_context"] = error_ctx
            if rate_limited:
                response["rate_limited"] = True
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
        threshold = get_stuck_threshold(runtime.session_stats)
        if len(hash_history) > threshold:
            runtime.pane_output_hashes[pane_id] = hash_history[-threshold:]
            hash_history = runtime.pane_output_hashes[pane_id]

        stuck = len(hash_history) >= threshold and len(set(hash_history[-threshold:])) == 1
        exited = not pane_alive  # reuse check from line 457

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
    "Send text to an agent pane. For answering questions or giving follow-up instructions.",
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
