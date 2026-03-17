"""Tools for verification, merging, and git branch management."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _extract_smart_output,
    _runtime,
    _safe_launch_pane,
    _tool_response,
)
from openmax.lead_agent.types import SubTask
from openmax.output import P, console


def _sanitize_branch_name(task_name: str) -> str:
    """Convert task name to a valid git branch name."""
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


def _branch_exists(cwd: str, branch_name: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0


def _worktree_is_valid(worktree_dir: Path) -> bool:
    return (worktree_dir / ".git").exists()


def _add_worktree(
    cwd: str,
    worktree_dir: Path,
    branch_name: str,
    *,
    cleanup_branch: bool = True,
) -> tuple[str | None, str | None]:
    """Add a git worktree for an existing branch. Returns (path, error)."""
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), branch_name],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        if cleanup_branch:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=cwd,
                capture_output=True,
                timeout=10,
            )
        return None, f"Failed to create worktree: {result.stderr.strip()}"
    return str(worktree_dir), None


def _create_agent_branch(cwd: str, branch_name: str) -> tuple[str | None, str | None]:
    """Create or reuse a git branch and worktree for an agent.

    Returns (worktree_path, error_message). On success error_message is None.
    """
    worktree_base = Path(cwd) / ".openmax-worktrees"
    worktree_dir = worktree_base / branch_name.replace("/", "_")

    try:
        if _branch_exists(cwd, branch_name) and _worktree_is_valid(worktree_dir):
            return str(worktree_dir), None

        if _branch_exists(cwd, branch_name):
            subprocess.run(["git", "worktree", "prune"], cwd=cwd, capture_output=True, timeout=10)
            if _worktree_is_valid(worktree_dir):
                return str(worktree_dir), None
            return _add_worktree(cwd, worktree_dir, branch_name, cleanup_branch=False)

        result = subprocess.run(
            ["git", "branch", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None, f"Failed to create branch: {result.stderr.strip()}"
        return _add_worktree(cwd, worktree_dir, branch_name, cleanup_branch=True)
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


def _git_run(
    args: list[str],
    cwd: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_conflict_files(merge_stderr: str) -> list[str]:
    return [
        line.split("Merge conflict in ")[-1].strip()
        for line in merge_stderr.splitlines()
        if "Merge conflict in " in line
    ]


def _merge_and_handle_conflicts(
    cwd: str,
    branch: str,
    target: str,
) -> tuple[str, str | None, list[str], str]:
    """Merge branch into target. Returns (status, commit_hash, conflict_files, diff)."""
    _git_run(["git", "checkout", target], cwd)
    merge = _git_run(["git", "merge", "--no-edit", branch], cwd, timeout=60)
    if merge.returncode == 0:
        head = _git_run(["git", "rev-parse", "HEAD"], cwd, timeout=10)
        return ("merged", head.stdout.strip(), [], "")
    diff = _git_run(["git", "diff", f"{target}...{branch}"], cwd, timeout=30)
    _git_run(["git", "merge", "--abort"], cwd)
    return ("conflict", None, _parse_conflict_files(merge.stderr), diff.stdout[:8000])


def _report_merge_success(branch: str, integration: str, short_hash: str) -> str:
    console.print(f"  [bold green]✓[/bold green]  Merged {branch} → {integration} ({short_hash})")
    _append_session_event(
        "tool.auto_merge",
        {"branch": branch, "status": "merged", "commit": short_hash},
    )
    return f"Merged {branch} → {integration} ({short_hash})"


def _report_merge_conflict(branch: str, conflict_files: list[str]) -> str:
    console.print(
        f"  [bold red]✗[/bold red]  Merge conflict for {branch}: {len(conflict_files)} file(s)"
    )
    _append_session_event(
        "tool.auto_merge",
        {"branch": branch, "status": "conflict", "files": conflict_files},
    )
    return (
        f"Merge conflict for {branch}: {', '.join(conflict_files)}. "
        "Use merge_agent_branch tool to resolve."
    )


def _auto_merge_branch(runtime: Any, subtask: SubTask) -> str:
    branch = subtask.branch_name
    if not branch:
        return ""
    integration = runtime.integration_branch or "main"
    try:
        status, commit_hash, files, _diff = _merge_and_handle_conflicts(
            runtime.cwd, branch, integration
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        console.print(f"  [bold red]✗[/bold red]  Auto-merge error: {e}")
        return f"Auto-merge error: {e}"
    if status == "merged":
        _cleanup_agent_branch(runtime.cwd, branch)
        return _report_merge_success(branch, integration, (commit_hash or "")[:8])
    return _report_merge_conflict(branch, files)


def _find_subtask_by_name(task_name: str) -> SubTask | None:
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            return st
    return None


def _merge_branch_result(
    task_name: str,
    status: str,
    commit_hash: str | None,
    conflict_files: list[str],
    diff: str,
    branch: str,
    integration: str,
) -> dict[str, Any]:
    if status == "merged":
        short = commit_hash[:8] if commit_hash else ""
        console.print(f"  [bold green]✓[/bold green]  Merged {branch} → {integration} ({short})")
        data: dict[str, Any] = {"status": "merged", "commit": commit_hash, "task_name": task_name}
    else:
        console.print(
            f"  [bold red]✗[/bold red]  Merge conflict for {branch}: {len(conflict_files)} file(s)"
        )
        data = {
            "status": "conflict",
            "task_name": task_name,
            "files": conflict_files,
            "diff": diff,
            "resolve_hint": (
                f"Dispatch a claude-code agent to: cd into the repo, run "
                f"`git merge {branch}`, read the conflict markers in each file, "
                f"understand the semantic intent of both sides from the diff above, "
                f"resolve intelligently, then `git add` and `git commit`."
            ),
        }
    _append_session_event("tool.merge_agent_branch", data)
    return data


def _merge_error_response(task_name: str, error: Exception) -> dict[str, Any]:
    msg = f"Git merge error: {error}"
    console.print(f"  [bold red]✗[/bold red]  {msg}")
    data: dict[str, Any] = {"status": "error", "task_name": task_name, "error": msg}
    _append_session_event("tool.merge_agent_branch", data)
    return _tool_response(data)


@tool(
    "run_verification",
    "Run a verification command (lint, test, build) and return structured pass/fail. "
    "On failure, includes a dispatch_hint field with error context — use it directly "
    "as the prompt when dispatching a debug agent. Returns {status, exit_code, output, "
    "duration_s, dispatch_hint?}.",
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
        return _tool_response("Error: empty command")

    wrapped_cmd = f'{command_str}; echo "__OPENMAX_EXIT_$?__"'
    shell_cmd = ["bash", "-c", wrapped_cmd]

    task_name = f"verify-{check_type}"
    pane, launch_err = _safe_launch_pane(
        runtime,
        command=shell_cmd,
        purpose=task_name,
        agent_type="command",
        title=f"openMax: verify {check_type}",
    )
    if pane is None:
        console.print(f"  [bold red]✗[/bold red]  verification launch failed: {launch_err}")
        return _tool_response({"status": "error", "error": launch_err, "check_type": check_type})

    console.print(
        f"  [bold cyan]{P}[/bold cyan]  verify {check_type}:"
        f" {command_str[:60]} → pane {pane.pane_id}"
    )

    start_ts = time.monotonic()
    deadline = start_ts + timeout
    exit_code: int | None = None
    output = ""
    text = ""

    while time.monotonic() < deadline:
        await anyio.sleep(2)
        try:
            text = runtime.pane_mgr.get_text(pane.pane_id)
        except Exception:
            text = ""
        match = re.search(r"__OPENMAX_EXIT_(\d+)__", text)
        if match:
            exit_code = int(match.group(1))
            output = text[: match.start()].strip()
            break
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

    capped_output = output[-2000:]
    result: dict[str, Any] = {
        "status": status,
        "check_type": check_type,
        "exit_code": exit_code,
        "output": capped_output,
        "duration_s": duration_s,
        "command": command_str,
    }

    if status != "pass":
        result["dispatch_hint"] = (
            f"The {check_type} check failed (exit code {exit_code}).\n"
            f"Command: {command_str}\n"
            f"Error output:\n{capped_output}\n\n"
            f"Investigate the root cause, fix the issue, re-run `{command_str}` "
            f"until it passes, then commit."
        )

    if status == "pass":
        console.print(
            f"  [bold green]✓[/bold green]  {check_type}: pass [dim]({duration_s}s)[/dim]"
        )
    else:
        console.print(f"  [bold red]✗[/bold red]  {check_type}: FAIL [dim]({duration_s}s)[/dim]")

    _append_session_event("tool.run_verification", result)
    return _tool_response(result)


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
    target = _find_subtask_by_name(task_name)
    if target is None:
        return _tool_response({"error": f"Task '{task_name}' not found"})
    if not target.branch_name:
        return _tool_response({"status": "skipped", "reason": "No branch for this task"})
    integration = runtime.integration_branch or "main"
    try:
        status, hash_, files, diff = _merge_and_handle_conflicts(
            runtime.cwd, target.branch_name, integration
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return _merge_error_response(task_name, e)
    if status == "merged":
        _cleanup_agent_branch(runtime.cwd, target.branch_name)
    return _tool_response(
        _merge_branch_result(task_name, status, hash_, files, diff, target.branch_name, integration)
    )
