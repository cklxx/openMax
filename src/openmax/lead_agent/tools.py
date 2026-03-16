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

from openmax.dashboard import P, console
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.memory import serialize_subtasks
from openmax.pane_manager import PaneManager
from openmax.session_runtime import (
    LeadAgentRuntime,
    anchor_payload,
    get_lead_agent_runtime,
    serialize_tasks,
)

_VALID_PHASE_TRANSITIONS: dict[str, set[str]] = {
    "research": {"implement"},
    "implement": {"verify"},
    "verify": {"finish", "implement"},  # allow re-dispatch via verify → implement
}


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
    """Poll pane output until a ready pattern appears or timeout.

    Uses two strategies:
    1. Pattern match — look for known ready strings in pane output.
    2. Output stability — if the pane has substantial output (≥3 lines)
       and output hasn't changed for 2 consecutive checks, treat as ready.
       This handles CLI version changes where patterns shift.
    """
    if not ready_patterns:
        return False
    deadline = time.monotonic() + timeout
    prev_text = ""
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            text = pane_mgr.get_text(pane_id)
        except Exception:
            text = ""
        # Strategy 1: explicit pattern match
        if any(pat in text for pat in ready_patterns):
            return True
        # Strategy 2: output stabilized with substantial content
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        if len(lines) >= 3 and text == prev_text:
            stable_count += 1
            if stable_count >= 2:
                return True
        else:
            stable_count = 0
        prev_text = text
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

    # Warn about file ownership overlaps within parallel groups
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

    # Store the plan submission
    runtime.plan_submitted = True

    plan_data = {
        "subtasks": subtasks_raw,
        "rationale": rationale,
        "parallel_groups": parallel_groups,
    }

    console.print(f"  [bold cyan]{P}[/bold cyan]  plan: {len(subtasks_raw)} subtasks")
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

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result_data),
            }
        ]
    }


def _sanitize_branch_name(task_name: str) -> str:
    """Convert task name to a valid git branch name."""
    import re

    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", task_name.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"openmax/{slug}" if slug else f"openmax/task-{int(time.time())}"


def _get_integration_branch(cwd: str) -> str | None:
    """Get the current git branch name, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _create_agent_branch(cwd: str, branch_name: str) -> tuple[str | None, str | None]:
    """Create a git branch and worktree for an agent.

    Returns (worktree_path, error_message). On success error_message is None.
    """
    worktree_base = Path(cwd) / ".openmax-worktrees"
    worktree_dir = worktree_base / branch_name.replace("/", "_")

    try:
        # Create branch from HEAD
        result = subprocess.run(
            ["git", "branch", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None, f"Failed to create branch: {result.stderr.strip()}"

        # Create worktree
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Clean up branch if worktree creation failed
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=cwd,
                capture_output=True,
                timeout=10,
            )
            return None, f"Failed to create worktree: {result.stderr.strip()}"

        return str(worktree_dir), None
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"Git error: {e}"


def _cleanup_agent_branch(cwd: str, branch_name: str) -> str | None:
    """Remove worktree and delete branch. Returns error message or None."""
    worktree_base = Path(cwd) / ".openmax-worktrees"
    worktree_dir = worktree_base / branch_name.replace("/", "_")

    try:
        if worktree_dir.exists():
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir), "--force"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Cleanup error: {e}"
    return None


def _auto_merge_branch(runtime: LeadAgentRuntime, subtask: SubTask) -> str:
    """Merge a subtask's branch into the integration branch. Returns status message."""
    branch = subtask.branch_name
    if not branch:
        return ""
    integration = runtime.integration_branch or "main"
    cwd = runtime.cwd
    try:
        subprocess.run(
            ["git", "checkout", integration],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        merge_result = subprocess.run(
            ["git", "merge", "--no-edit", branch],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if merge_result.returncode == 0:
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit_hash = hash_result.stdout.strip()[:8]
            _cleanup_agent_branch(cwd, branch)
            console.print(
                f"  [bold green]\u2713[/bold green]  Merged {branch} \u2192 {integration}"
                f" ({commit_hash})"
            )
            _append_session_event(
                "tool.auto_merge",
                {"branch": branch, "status": "merged", "commit": commit_hash},
            )
            return f"Merged {branch} \u2192 {integration} ({commit_hash})"

        # Conflict — abort and report
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        conflict_files = [
            line.split("Merge conflict in ")[-1].strip()
            for line in merge_result.stdout.splitlines()
            if "Merge conflict in " in line
        ]
        console.print(
            f"  [bold red]\u2717[/bold red]  Merge conflict for {branch}:"
            f" {len(conflict_files)} file(s)"
        )
        _append_session_event(
            "tool.auto_merge",
            {"branch": branch, "status": "conflict", "files": conflict_files},
        )
        return (
            f"Merge conflict for {branch}: {', '.join(conflict_files)}. "
            "Use merge_agent_branch tool to resolve."
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        console.print(f"  [bold red]\u2717[/bold red]  Auto-merge error: {e}")
        return f"Auto-merge error: {e}"


def _try_reuse_done_pane(
    runtime: LeadAgentRuntime, agent_type: str, task_name: str
) -> SimpleNamespace | None:
    """Find a done pane with the same agent type to reuse.

    Only reuses a pane if no other running/pending task is already occupying it.
    This prevents multiple parallel tasks from being funneled into the same pane.
    """
    # Collect pane IDs that are currently in use by running or pending tasks.
    busy_panes: set[int] = set()
    for st in runtime.plan.subtasks:
        if st.status in (TaskStatus.RUNNING, TaskStatus.PENDING) and st.pane_id is not None:
            busy_panes.add(st.pane_id)

    for st in runtime.plan.subtasks:
        if (
            st.agent_type == agent_type
            and st.status == TaskStatus.DONE
            and st.pane_id is not None
            and st.pane_id not in busy_panes
            and runtime.pane_mgr.is_pane_alive(st.pane_id)
        ):
            console.print(f"  [dim]{P}  reusing pane {st.pane_id} for {task_name}[/dim]")
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
                f"  [yellow]![/yellow]Pane {pane.pane_id} ({agent_type}) "
                "did not show ready signal within timeout \u2014 sending prompt anyway"
            )
    else:
        await anyio.sleep(cmd_spec.ready_delay_seconds)
    runtime.pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)


def _compress_context(context: str, budget: int) -> str:
    """Compress context text to fit within an approximate token budget.

    Uses len(text)//4 as a rough token estimate. If over budget, keeps the
    first paragraph and as many subsequent bullet/numbered-list lines as fit.
    """
    approx_tokens = len(context) // 4
    if approx_tokens <= budget:
        return context

    char_budget = budget * 4
    lines = context.split("\n")

    # Always keep the first paragraph (up to first blank line)
    kept: list[str] = []
    i = 0
    while i < len(lines):
        kept.append(lines[i])
        if lines[i].strip() == "" and i > 0:
            i += 1
            break
        i += 1

    # Then greedily add bullet/heading lines that fit
    for line in lines[i:]:
        stripped = line.lstrip()
        is_key_line = stripped.startswith(("-", "*", "#")) or (
            len(stripped) > 1 and stripped[0].isdigit() and stripped[1] in ".)"
        )
        if not is_key_line:
            continue
        candidate = "\n".join(kept + [line])
        if len(candidate) <= char_budget:
            kept.append(line)
        else:
            break

    result = "\n".join(kept)
    if len(result) > char_budget:
        result = result[:char_budget].rsplit("\n", 1)[0]
    return result


def _build_subagent_context(
    *,
    branch_name: str | None,
    memory_text: str | None,
) -> str:
    """Build a structured context block for sub-agent prompts.

    Returns empty string when there is nothing to inject.
    """
    sections: list[str] = []

    if branch_name:
        sections.append(
            f"Branch: {branch_name} (isolated worktree — commit here, do not switch branches)"
        )

    if memory_text:
        sections.append(f"Relevant history:\n{memory_text}")

    if not sections:
        return ""

    header = "## Context (auto-injected by openMax — use only if relevant)"
    return "\n\n" + header + "\n\n" + "\n\n".join(sections)


@tool(
    "dispatch_agent",
    "Dispatch a sub-task to an AI agent in a terminal pane. "
    "The prompt is the agent's ONLY context \u2014 include file paths, constraints, "
    "and any knowledge it cannot discover on its own. "
    "All agents share one window with smart grid layout. Returns pane_id.",
    {
        "task_name": str,
        "agent_type": str,
        "prompt": str,
        "override_reason": str,
        "retry_count": int,
        "context_budget_tokens": int,
        "token_budget": int,
    },
)
async def dispatch_agent(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]
    agent_type = args.get("agent_type", "claude-code")
    prompt = args["prompt"]
    retry_count = args.get("retry_count", 0)
    token_budget = args.get("token_budget")
    if not isinstance(retry_count, int) or retry_count < 0:
        retry_count = 0

    # For retries, allow re-dispatch of the same task (update in place).
    # Otherwise, prevent duplicate task names with auto-suffix.
    existing_names = {st.name for st in runtime.plan.subtasks}
    is_retry = retry_count > 0 and task_name in existing_names
    if task_name in existing_names and not is_retry:
        suffix = 2
        while f"{task_name}-{suffix}" in existing_names:
            suffix += 1
        original = task_name
        task_name = f"{task_name}-{suffix}"
        console.print(
            f"  [yellow]![/yellow]Task '{original}' already exists, renamed to '{task_name}'"
        )

    # Auto-derive recommended agent from memory rankings
    recommended_agent = None
    if runtime.memory_store is not None:
        try:
            rankings = runtime.memory_store.derive_agent_rankings(cwd=runtime.cwd, task=task_name)
            if rankings:
                recommended_agent = rankings[0].agent_type
        except Exception:
            pass

    # Enforce allowed agents constraint
    if runtime.allowed_agents:
        if agent_type not in runtime.allowed_agents:
            fallback = runtime.allowed_agents[0]
            console.print(
                f"  [yellow]![/yellow]Agent '{agent_type}' not allowed, using '{fallback}' instead"
            )
            agent_type = fallback

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

    # Gather memory context (compressed)
    memory_text: str | None = None
    context_budget = args.get("context_budget_tokens", 2000)
    if not isinstance(context_budget, int) or context_budget < 0:
        context_budget = 2000
    if runtime.memory_store is not None:
        try:
            memory_context = runtime.memory_store.build_context(
                cwd=runtime.cwd,
                task=task_name,
            )
            if memory_context and memory_context.text:
                memory_text = _compress_context(memory_context.text, context_budget)
        except Exception:
            pass  # Don't fail dispatch if memory lookup fails

    # -- Branch isolation: create per-agent branch + worktree --
    branch_name: str | None = None
    agent_cwd = runtime.cwd

    if runtime.integration_branch is None:
        runtime.integration_branch = _get_integration_branch(runtime.cwd)

    branch_name_candidate = _sanitize_branch_name(task_name)
    worktree_path, branch_err = _create_agent_branch(runtime.cwd, branch_name_candidate)
    if worktree_path is not None:
        branch_name = branch_name_candidate
        agent_cwd = worktree_path
        console.print(f"  [dim]{P}  branch {branch_name} → {worktree_path}[/dim]")
    elif branch_err:
        console.print(f"  [yellow]![/yellow]  Branch isolation skipped: {branch_err}")

    # Inject structured context (git branch + memory)
    context_block = _build_subagent_context(
        branch_name=branch_name,
        memory_text=memory_text,
    )
    if context_block:
        prompt = prompt + context_block

    cmd_spec = adapter.get_command(prompt, cwd=agent_cwd)
    launch_env = cmd_spec.env or None

    # -- Try to reuse a done pane with the same agent type --
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
            "cwd": agent_cwd,
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
            "cwd": agent_cwd,
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
        retry_count=retry_count,
        branch_name=branch_name,
        started_at=time.time(),
        token_budget=token_budget,
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
        f"  [bold cyan]{P}[/bold cyan]  [bold]{task_name}[/bold]"
        f" \u2192 pane {pane.pane_id} [dim]({agent_type})[/dim]"
    )
    if retry_count > 0:
        console.print(f"  [yellow]↻[/yellow]  Retrying '{task_name}' (attempt {retry_count + 1})")
    event_payload = {
        "task_name": task_name,
        "agent_type": agent_type,
        "prompt": prompt,
        "pane_id": pane.pane_id,
        "window_id": runtime.agent_window_id,
        "panes_in_window": pane_count,
        "recommended_agent": recommended_agent,
        "retry_count": retry_count,
        "branch_name": branch_name,
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
                        "retry_count": retry_count,
                        "branch_name": branch_name,
                        "token_budget": token_budget,
                    }
                ),
            }
        ]
    }


def _check_budget_warning(task_name: str, used: int, budget: int) -> str | None:
    """Check token budget and return warning level if threshold exceeded."""
    if used >= budget:
        return "hard_limit"
    if used >= 0.8 * budget:
        return "soft_limit"
    return None


_STUCK_THRESHOLD = 3  # consecutive identical outputs to trigger stuck
_MAX_RETRIES = 2


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


@tool(
    "read_pane_output",
    "Read the current terminal output of an agent pane (~100 tail lines). "
    "Error lines from earlier output appear at the top with [ERROR] prefix. "
    "Returns JSON with 'text', 'stuck', and 'exited' fields. "
    "When exited=true, includes retry_count, max_retries, and can_retry. "
    "stuck=true when output is unchanged for 3 consecutive reads (~60-90s).",
    {"pane_id": int},
)
async def read_pane_output(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pane_id = args["pane_id"]
    try:
        pane_alive = runtime.pane_mgr.is_pane_alive(pane_id)

        # get_text returns cached output even if pane is dead.
        try:
            text = runtime.pane_mgr.get_text(pane_id)
        except Exception:
            text = ""

        if not pane_alive:
            text = _extract_smart_output(text, tail_lines=100) if text else ""
            # Look up subtask retry info for the exited pane
            retry_info = _get_retry_info_for_pane(pane_id)
            response: dict[str, Any] = {
                "text": text or "(pane no longer exists)",
                "stuck": False,
                "exited": True,
            }
            response.update(retry_info)
            result = json.dumps(response)
            event_payload: dict[str, Any] = {
                "pane_id": pane_id,
                "preview": text[:500],
                "stuck": False,
                "exited": True,
            }
            event_payload.update(retry_info)
            _append_session_event("tool.read_pane_output", event_payload)
            return {"content": [{"type": "text", "text": result}]}

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

        response_data: dict[str, Any] = {
            "text": text,
            "stuck": stuck,
            "exited": exited,
        }

        # Add budget status if a budget is set for this pane's subtask
        for st in runtime.plan.subtasks:
            if st.pane_id == pane_id and st.token_budget is not None:
                warning = _check_budget_warning(st.name, st.tokens_used, st.token_budget)
                response_data["budget"] = {
                    "token_budget": st.token_budget,
                    "tokens_used": st.tokens_used,
                    "warning": warning,
                }
                break

        result = json.dumps(response_data)
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
    if not runtime.pane_mgr.is_pane_alive(pane_id):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: pane {pane_id} no longer exists. "
                    "Re-dispatch the task to a new pane.",
                }
            ]
        }
    runtime.pane_mgr.send_text(pane_id, text)
    # core.py already prints the intervention line.
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

            # Auto-merge branch back to integration branch if isolated
            merge_note = ""
            if st.branch_name:
                merge_note = _auto_merge_branch(runtime, st)

            msg = f"Marked '{task_name}' as done"
            if merge_note:
                msg += f". {merge_note}"
            return {"content": [{"type": "text", "text": msg}]}
    return {"content": [{"type": "text", "text": f"Task '{task_name}' not found"}]}


@tool(
    "merge_agent_branch",
    "Merge an agent's branch back to the integration branch. "
    "Call after mark_task_done when the agent worked on an isolated branch. "
    "Returns {status, commit} on success or {status, files, diff} on conflict.",
    {"task_name": str},
)
async def merge_agent_branch(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task_name = args["task_name"]

    # Find the subtask
    target: SubTask | None = None
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            target = st
            break

    if target is None:
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": f"Task '{task_name}' not found"})}
            ]
        }

    if not target.branch_name:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"status": "skipped", "reason": "No branch for this task"}),
                }
            ]
        }

    integration = runtime.integration_branch or "main"
    cwd = runtime.cwd
    branch = target.branch_name

    try:
        # Ensure we're on the integration branch
        subprocess.run(
            ["git", "checkout", integration],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Attempt merge
        merge_result = subprocess.run(
            ["git", "merge", "--no-edit", branch],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if merge_result.returncode == 0:
            # Get merge commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit_hash = hash_result.stdout.strip()

            # Clean up worktree and branch
            _cleanup_agent_branch(cwd, branch)

            console.print(
                f"  [bold green]\u2713[/bold green]  Merged {branch} \u2192 {integration}"
                f" ({commit_hash[:8]})"
            )

            result_data = {
                "status": "merged",
                "commit": commit_hash,
                "task_name": task_name,
            }
            _append_session_event("tool.merge_agent_branch", result_data)
            return {"content": [{"type": "text", "text": json.dumps(result_data)}]}

        # Merge conflict — abort and report
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Parse conflict files from merge output
        conflict_files: list[str] = []
        for line in merge_result.stdout.splitlines():
            if line.startswith("CONFLICT"):
                parts = line.split("Merge conflict in ")
                if len(parts) > 1:
                    conflict_files.append(parts[1].strip())

        diff_text = merge_result.stdout[:2000]
        console.print(
            f"  [bold red]\u2717[/bold red]  Merge conflict for {branch}:"
            f" {len(conflict_files)} file(s)"
        )

        result_data = {
            "status": "conflict",
            "task_name": task_name,
            "files": conflict_files,
            "diff": diff_text,
        }
        _append_session_event("tool.merge_agent_branch", result_data)
        return {"content": [{"type": "text", "text": json.dumps(result_data)}]}

    except (OSError, subprocess.TimeoutExpired) as e:
        error_msg = f"Git merge error: {e}"
        console.print(f"  [bold red]\u2717[/bold red]  {error_msg}")
        result_data = {"status": "error", "task_name": task_name, "error": error_msg}
        _append_session_event("tool.merge_agent_branch", result_data)
        return {"content": [{"type": "text", "text": json.dumps(result_data)}]}


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
    # No console output \u2014 internal bookkeeping, not user-facing.
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

    # Validate from_phase matches current phase on the runtime
    current = runtime.current_phase
    if from_phase != current:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Error: from_phase '{from_phase}' does not match current phase '{current}'"
                    ),
                }
            ]
        }

    # Validate to_phase is a valid next phase
    allowed = _VALID_PHASE_TRANSITIONS.get(from_phase)
    if allowed is None or to_phase not in allowed:
        valid_str = ", ".join(sorted(allowed)) if allowed else "none"
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Error: invalid transition '{from_phase}' → '{to_phase}'. "
                        f"Valid next phases: {valid_str}"
                    ),
                }
            ]
        }

    # Update current phase on the runtime
    runtime.current_phase = to_phase

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
    # No console output \u2014 internal bookkeeping.
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
    from rich.panel import Panel

    pct_color = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    panel = Panel(
        f"  [{pct_color}]{pct}%[/{pct_color}] complete\n  {notes}",
        title="[bold]Result[/bold]",
        title_align="left",
        border_style="dim cyan",
        padding=(0, 2),
    )
    console.print()
    console.print(panel)
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
    "Use when the goal is genuinely ambiguous or you need a decision "
    "only the user can make. Do NOT use for routine confirmations. "
    "Pass choices as a list of options \u2014 the user can pick by number "
    "or type a free-form answer.",
    {"question": str, "choices": list},
)
async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    question = args["question"]
    raw_choices = args.get("choices") or []
    if isinstance(raw_choices, str):
        try:
            raw_choices = json.loads(raw_choices)
        except (json.JSONDecodeError, ValueError):
            raw_choices = [raw_choices]
    choices: list[str] = list(raw_choices)

    # Pause the dashboard so the prompt is visible
    if runtime.dashboard is not None:
        runtime.dashboard.stop()

    console.print(f"\n  [bold yellow]?[/bold yellow]  [bold]{question}[/bold]")
    if choices:
        for i, choice in enumerate(choices, 1):
            console.print(f"    [bold]{i}.[/bold] {choice}")
        console.print("    [dim]Enter a number or type your own answer[/dim]")
    raw: str = await anyio.to_thread.run_sync(lambda: input("Your answer: "))
    raw = raw.strip()

    # Resolve numbered choice
    answer = raw
    if choices and raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            answer = choices[idx]
            console.print(f"  [dim]\u2192 {answer}[/dim]")

    # Resume the dashboard
    if runtime.dashboard is not None:
        runtime.dashboard.start()

    _append_session_event(
        "tool.ask_user",
        {"question": question, "choices": choices, "answer": answer},
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
    await anyio.sleep(seconds)
    return {"content": [{"type": "text", "text": f"Waited {seconds}s"}]}


@tool(
    "run_command",
    "Run a CLI command in a terminal pane. For build/test/git/servers/databases \u2014 "
    "NOT for file search or exploration (use find_files/grep_files/read_file instead). "
    "Set interactive=true for long-running programs, false (default) for one-shot.",
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
    console.print(
        f"  [bold cyan]{P}[/bold cyan]  [bold]{command_str[:60]}[/bold] \u2192 pane {pane.pane_id}"
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
        f"  [bold cyan]{P}[/bold cyan]  verify {check_type}:"
        f" {command_str[:60]} \u2192 pane {pane.pane_id}"
    )

    # Poll for the exit marker
    start_ts = time.monotonic()
    deadline = start_ts + timeout
    exit_code: int | None = None
    output = ""

    import re

    while time.monotonic() < deadline:
        await anyio.sleep(2)
        try:
            text = runtime.pane_mgr.get_text(pane.pane_id)
        except Exception:
            text = ""
        # Look for our exit marker
        match = re.search(r"__OPENMAX_EXIT_(\d+)__", text)
        if match:
            exit_code = int(match.group(1))
            output = text[: match.start()].strip()
            break
        # If pane died, cached output is all we'll ever get — stop polling.
        if not runtime.pane_mgr.is_pane_alive(pane.pane_id):
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

    if status == "pass":
        console.print(
            f"  [bold green]\u2713[/bold green]  {check_type}: pass [dim]({duration_s}s)[/dim]"
        )
    else:
        console.print(
            f"  [bold red]\u2717[/bold red]  {check_type}: FAIL [dim]({duration_s}s)[/dim]"
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

    if has_conflict:
        console.print("  [bold red]\u2717[/bold red]  conflicts found")
    else:
        console.print("  [bold green]\u2713[/bold green]  no conflicts")
    _append_session_event("tool.check_conflicts", result)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


# ── File exploration tools (instant, no pane needed) ──────────────────


@tool(
    "find_files",
    "Search for files by glob pattern in the working directory. "
    "Returns matching file paths instantly (no pane needed). Max 200 results. "
    "Examples: '**/*.md', 'src/**/*.py', '**/roadmap*', 'docs/**'.",
    {"pattern": str, "path": str},
)
async def find_files_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pattern = args["pattern"]
    rel_path = args.get("path", ".")

    target_dir = (Path(runtime.cwd) / rel_path).resolve()
    cwd_resolved = Path(runtime.cwd).resolve()
    if not str(target_dir).startswith(str(cwd_resolved)):
        return {"content": [{"type": "text", "text": "Error: path outside working directory"}]}

    try:
        matches = sorted(target_dir.glob(pattern))
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    filtered = [
        m
        for m in matches
        if not any(part.startswith(".") for part in m.relative_to(cwd_resolved).parts)
        and "__pycache__" not in str(m)
    ][:200]

    rel_paths = [str(m.relative_to(cwd_resolved)) for m in filtered]
    result = f"Found {len(rel_paths)} file(s):\n" + "\n".join(rel_paths)
    console.print(f"  [dim]{P}  find '{pattern}' \u2192 {len(rel_paths)} file(s)[/dim]")
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "grep_files",
    "Search file contents for a regex pattern. Returns matching lines with "
    "file paths and line numbers instantly (no pane needed). Max 100 matches. "
    "Use glob param to filter files (e.g. '**/*.py').",
    {"pattern": str, "glob": str, "max_results": int},
)
async def grep_files_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pattern = args["pattern"]
    file_glob = args.get("glob", "**/*")
    max_results = min(args.get("max_results", 100), 200)

    import re

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return {"content": [{"type": "text", "text": f"Error: invalid regex: {e}"}]}

    cwd_resolved = Path(runtime.cwd).resolve()
    matches: list[str] = []

    try:
        for filepath in sorted(cwd_resolved.glob(file_glob)):
            if not filepath.is_file():
                continue
            if any(part.startswith(".") for part in filepath.relative_to(cwd_resolved).parts):
                continue
            if "__pycache__" in str(filepath):
                continue
            try:
                text = filepath.read_text(errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            rel = str(filepath.relative_to(cwd_resolved))
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    if not matches:
        result = f"No matches for pattern '{pattern}'"
    else:
        result = f"Found {len(matches)} match(es):\n" + "\n".join(matches)

    console.print(f"  [dim]{P}  grep '{pattern}' \u2192 {len(matches)} match(es)[/dim]")
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "read_file",
    "Read a file from the working directory. Returns file content instantly "
    "(no pane needed, max 2000 lines). Specify offset/limit for large files.",
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

    console.print(f"  [dim]{P}  read {rel_path} ({len(selected)}/{total} lines)[/dim]")
    return {"content": [{"type": "text", "text": result}]}


# All tool objects for easy collection
ALL_TOOLS = [
    ask_user,
    check_conflicts,
    dispatch_agent,
    find_files_tool,
    get_agent_recommendations,
    grep_files_tool,
    merge_agent_branch,
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
