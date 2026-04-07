"""Tools for verification and merging."""

from __future__ import annotations

import logging
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import tool

from openmax.lead_agent.tools._branch import (
    _cleanup_agent_branch,
    _get_merge_lock,
)
from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _extract_smart_output,
    _runtime,
    _safe_launch_pane,
    _tool_response,
)
from openmax.lead_agent.tools._merge import (
    choose_merge_strategy,
    do_rebase,
    try_auto_resolve_conflicts,
)
from openmax.lead_agent.types import SubTask
from openmax.output import P, console
from openmax.project_tools import ProjectTooling, detect_all_tooling
from openmax.stats import load_stats, save_stats, update_stats
from openmax.test_parsing import parse_test_output

logger = logging.getLogger(__name__)

_VERIFY_POLL_INITIAL = 0.2
_VERIFY_POLL_BACKOFF = 1.2
_VERIFY_POLL_MAX = 1.0


def _poll_exit_marker(runtime: Any, pane_id: int, prev_text: str) -> tuple[int | None, str, bool]:
    """Check pane output for exit marker. Returns (exit_code, text, pane_exited)."""
    try:
        text = runtime.pane_mgr.get_text(pane_id)
    except Exception:
        logger.debug("Pane %d poll failed", pane_id, exc_info=True)
        text = prev_text
    match = re.search(r"__OPENMAX_EXIT_(\d+)__", text)
    if match:
        return int(match.group(1)), text[: match.start()].strip(), True
    if not runtime.pane_mgr.is_pane_alive(pane_id):
        return None, text, True
    return None, text, False


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


def _count_new_commits(cwd: str, target: str, branch: str) -> int:
    log = _git_run(["git", "log", f"{target}..{branch}", "--oneline"], cwd, timeout=10)
    return len([line for line in log.stdout.splitlines() if line.strip()])


def _try_rebase_strategy(
    cwd: str,
    branch: str,
    target: str,
    commit_count: int,
) -> tuple[str, str | None, list[str], str, int]:
    """Attempt rebase strategy. Falls back to merge on failure."""
    success, err = do_rebase(cwd, branch, target)
    if success:
        _git_run(["git", "checkout", target], cwd)
        ff = _git_run(["git", "merge", "--ff-only", branch], cwd, timeout=60)
        if ff.returncode == 0:
            head = _git_run(["git", "rev-parse", "HEAD"], cwd, timeout=10)
            console.print(f"  [dim]  Rebased {branch} onto {target} (linear)[/dim]")
            return ("merged", head.stdout.strip(), [], "", commit_count)
    return _try_merge_strategy(cwd, branch, target, commit_count)


def _try_merge_strategy(
    cwd: str,
    branch: str,
    target: str,
    commit_count: int,
) -> tuple[str, str | None, list[str], str, int]:
    """Standard merge with auto-resolve for trivial conflicts."""
    _git_run(["git", "checkout", target], cwd)
    merge = _git_run(["git", "merge", "--no-edit", branch], cwd, timeout=60)
    if merge.returncode == 0:
        head = _git_run(["git", "rev-parse", "HEAD"], cwd, timeout=10)
        return ("merged", head.stdout.strip(), [], "", commit_count)
    resolved, unresolved = try_auto_resolve_conflicts(cwd)
    if resolved and not unresolved:
        _git_run(["git", "commit", "--no-edit"], cwd, timeout=30)
        head = _git_run(["git", "rev-parse", "HEAD"], cwd, timeout=10)
        console.print(f"  [dim]  Auto-resolved {len(resolved)} trivial conflict(s)[/dim]")
        return ("merged", head.stdout.strip(), [], "", commit_count)
    diff = _git_run(["git", "diff", f"{target}...{branch}"], cwd, timeout=30)
    _git_run(["git", "merge", "--abort"], cwd)
    return ("conflict", None, _parse_conflict_files(merge.stderr), diff.stdout[:8000], commit_count)


def _merge_and_handle_conflicts(
    cwd: str,
    branch: str,
    target: str,
) -> tuple[str, str | None, list[str], str, int]:
    """Merge branch into target using intelligent strategy selection.

    Returns (status, hash, conflict_files, diff, commit_count).
    """
    commit_count = _count_new_commits(cwd, target, branch)
    if commit_count == 0:
        console.print(f"  [dim]  Branch {branch} has no new commits — skipping merge[/dim]")
        head = _git_run(["git", "rev-parse", "HEAD"], cwd, timeout=10)
        return ("no-op", head.stdout.strip(), [], "", 0)
    strategy = choose_merge_strategy(branch, target, cwd)
    if strategy == "rebase":
        return _try_rebase_strategy(cwd, branch, target, commit_count)
    return _try_merge_strategy(cwd, branch, target, commit_count)


def _find_subtask_by_name(task_name: str) -> SubTask | None:
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            return st
    return None


def _update_merge_stats(cwd: str, conflict_files: list[str], had_conflict: bool) -> None:
    """Record merge outcome in SessionStats."""
    try:
        stats = load_stats(cwd)
        dirs_rates: dict[str, float] = {}
        if had_conflict:
            for f in conflict_files:
                d = str(Path(f).parent) if "/" in f else "."
                dirs_rates[d] = 1.0
        updated = update_stats(stats, {"merge_conflict_rate_by_dir": dirs_rates})
        save_stats(updated, cwd)
    except Exception:
        logger.debug("Merge stats persistence failed", exc_info=True)


def _merge_branch_result(
    task_name: str,
    status: str,
    commit_hash: str | None,
    conflict_files: list[str],
    diff: str,
    branch: str,
    integration: str,
    commit_count: int,
    cwd: str = "",
) -> dict[str, Any]:
    if status == "no-op":
        console.print(f"  [dim]  {branch} — no new commits, skipped[/dim]")
        data: dict[str, Any] = {
            "status": "no-op",
            "task_name": task_name,
            "commit_count": 0,
        }
    elif status == "merged":
        short = commit_hash[:8] if commit_hash else ""
        console.print(
            f"  [bold green]✓[/bold green]  Merged {branch} → {integration}"
            f" ({short}) [{commit_count} commits]"
        )
        data = {
            "status": "merged",
            "commit": commit_hash,
            "task_name": task_name,
            "commit_count": commit_count,
        }
        if cwd:
            _update_merge_stats(cwd, [], had_conflict=False)
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
        if cwd:
            _update_merge_stats(cwd, conflict_files, had_conflict=True)
    _append_session_event("tool.merge_agent_branch", data)
    return data


def _merge_error_response(task_name: str, error: Exception) -> dict[str, Any]:
    msg = f"Git merge error: {error}"
    console.print(f"  [bold red]✗[/bold red]  {msg}")
    data: dict[str, Any] = {"status": "error", "task_name": task_name, "error": msg}
    _append_session_event("tool.merge_agent_branch", data)
    return _tool_response(data)


def _cmd_for_check_type(tooling: ProjectTooling, check_type: str) -> str | None:
    """Extract the command from a ProjectTooling based on check_type."""
    if check_type in ("lint", "format"):
        return tooling.lint_cmd
    if check_type == "test":
        return tooling.test_cmd
    return tooling.lint_cmd or tooling.test_cmd


def _resolve_commands(
    cwd: str,
    check_type: str,
    command: str | None,
) -> list[tuple[str, str | None]]:
    """Resolve verification commands. Returns list of (command, language) pairs."""
    if command:
        return [(command, None)]
    multi = detect_all_tooling(cwd)
    if not multi.toolings:
        return []
    pairs: list[tuple[str, str | None]] = []
    for tooling in multi.toolings:
        cmd = _cmd_for_check_type(tooling, check_type)
        if cmd:
            pairs.append((cmd, tooling.language))
    return pairs


async def _run_single_check(
    runtime: Any,
    check_type: str,
    command_str: str,
    timeout: int,
    language: str | None,
) -> dict[str, Any]:
    """Execute one verification command and return its result dict."""
    label = f"{language}:{check_type}" if language else check_type

    wrapped_cmd = f'{command_str}; _exit=$?; echo "__OPENMAX_EXIT_${{_exit}}__"; sleep 5'
    shell_cmd = ["bash", "-c", wrapped_cmd]

    # Ensure src/ is on PYTHONPATH so editable-installed packages are importable
    src_dir = str(Path(runtime.cwd) / "src")
    env: dict[str, str] = {}
    if Path(src_dir).is_dir():
        env["PYTHONPATH"] = src_dir

    task_name = f"verify-{label}"
    pane, launch_err = _safe_launch_pane(
        runtime,
        command=shell_cmd,
        purpose=task_name,
        agent_type="command",
        title=f"openMax: verify {label}",
        cwd=runtime.cwd,
        env=env,
    )
    if pane is None:
        console.print(f"  [bold red]✗[/bold red]  verification launch failed: {launch_err}")
        return {"status": "error", "error": launch_err, "check_type": label}

    console.print(
        f"  [bold cyan]{P}[/bold cyan]  verify {label}: {command_str[:60]} → pane {pane.pane_id}"
    )

    start_ts = time.monotonic()
    deadline = start_ts + timeout
    exit_code: int | None = None
    output = ""
    text = ""

    poll_interval = _VERIFY_POLL_INITIAL
    while time.monotonic() < deadline:
        await anyio.sleep(poll_interval)
        poll_interval = min(poll_interval * _VERIFY_POLL_BACKOFF, _VERIFY_POLL_MAX)
        found_code, latest_text, done = _poll_exit_marker(runtime, pane.pane_id, text)
        text = latest_text or text
        if found_code is not None:
            exit_code = found_code
            output = text
            break
        if done:
            break

    duration_s = int(time.monotonic() - start_ts)

    if exit_code is None:
        # Pane exited without exit marker — infer from output content
        smart_out = _extract_smart_output(text, tail_lines=50) if text else ""
        from openmax.lead_agent.tools._error_context import extract_error_context

        error_ctx = extract_error_context(text) if text else ""
        markers = ("Error", "FAILED", "Traceback")
        has_errors = bool(error_ctx and any(m in error_ctx for m in markers))
        status = "fail" if has_errors else "inconclusive"
        output = smart_out
    elif exit_code == 0:
        status = "pass"
    else:
        status = "fail"

    if not output and text:
        output = _extract_smart_output(text, tail_lines=50)

    capped_output = output[-2000:]

    if check_type == "test":
        tr = parse_test_output(output)
        test_results: dict[str, Any] = {
            "passed": tr.passed,
            "failed": tr.failed,
            "skipped": tr.skipped,
            "errors": tr.errors,
            "failure_summaries": tr.failure_summaries,
            "framework": tr.framework,
        }
    else:
        test_results = None

    result: dict[str, Any] = {
        "status": status,
        "check_type": label,
        "exit_code": exit_code,
        "output": capped_output,
        "duration_s": duration_s,
        "command": command_str,
    }
    if language:
        result["language"] = language
    if test_results is not None:
        result["test_results"] = test_results

    if status != "pass":
        result["dispatch_hint"] = (
            f"The {label} check failed (exit code {exit_code}).\n"
            f"Command: {command_str}\n"
            f"Error output:\n{capped_output}\n\n"
            f"Investigate the root cause, fix the issue, re-run `{command_str}` "
            f"until it passes, then commit."
        )

    if status == "pass":
        console.print(f"  [bold green]✓[/bold green]  {label}: pass [dim]({duration_s}s)[/dim]")
    elif status == "inconclusive":
        console.print(
            f"  [bold yellow]?[/bold yellow]  {label}: inconclusive [dim]({duration_s}s)[/dim]"
        )
    else:
        console.print(f"  [bold red]✗[/bold red]  {label}: FAIL [dim]({duration_s}s)[/dim]")

    _append_session_event("tool.run_verification", result)
    return result


async def _run_checks_parallel(
    runtime: Any,
    check_type: str,
    commands: list[tuple[str, str | None]],
    timeout: int,
) -> list[dict[str, Any]]:
    """Run multiple verification commands concurrently in separate panes."""
    results: list[dict[str, Any]] = [{}] * len(commands)

    async def _run_at(idx: int, cmd: str, lang: str | None) -> None:
        results[idx] = await _run_single_check(runtime, check_type, cmd, timeout, lang)

    async with anyio.create_task_group() as tg:
        for i, (cmd, lang) in enumerate(commands):
            tg.start_soon(_run_at, i, cmd, lang)
    return results


@tool(
    "run_verification",
    "Run lint/test/build check. Auto-detects tooling if command omitted. "
    "On failure, dispatch_hint field contains debug agent prompt.",
    {
        "check_type": str,
        "command": str,
        "timeout": int,
    },
)
async def run_verification(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    check_type = args.get("check_type", "custom")
    command_str = args.get("command")
    timeout = min(max(args.get("timeout", 120), 10), 600)

    commands = _resolve_commands(runtime.cwd, check_type, command_str)
    if not commands:
        return _tool_response(
            {"status": "error", "error": "No verification command and no tooling detected"}
        )

    if len(commands) == 1:
        cmd, lang = commands[0]
        result = await _run_single_check(runtime, check_type, cmd, timeout, lang)
        return _tool_response(result)

    results = await _run_checks_parallel(runtime, check_type, commands, timeout)

    overall = "pass" if all(r["status"] == "pass" for r in results) else "fail"
    total_duration = sum(r.get("duration_s", 0) for r in results)
    combined: dict[str, Any] = {
        "status": overall,
        "check_type": check_type,
        "duration_s": total_duration,
        "languages": [r.get("language", "unknown") for r in results],
        "results": results,
    }
    if overall != "pass":
        failed = [r for r in results if r["status"] != "pass"]
        hints = [r["dispatch_hint"] for r in failed if "dispatch_hint" in r]
        combined["dispatch_hint"] = "\n---\n".join(hints)

    return _tool_response(combined)


def _do_merge(
    cwd: str,
    branch: str,
    integration: str,
) -> tuple[str, str | None, list[str], str, int]:
    """Run merge synchronously (meant to run in a worker thread). Cleanup is deferred."""
    return _merge_and_handle_conflicts(cwd, branch, integration)


def _defer_branch_cleanup(runtime: Any, branch: str) -> None:
    """Queue a branch for deferred cleanup instead of blocking the merge return."""
    if not hasattr(runtime, "_deferred_branches"):
        runtime._deferred_branches = []
    runtime._deferred_branches.append(branch)


def cleanup_deferred_branches(runtime: Any) -> None:
    """Batch-cleanup branches that were deferred during merge."""
    branches = getattr(runtime, "_deferred_branches", [])
    for branch in branches:
        try:
            _cleanup_agent_branch(runtime.cwd, branch)
        except Exception:
            logger.debug("Branch cleanup failed for %s", branch, exc_info=True)
    runtime._deferred_branches = []


async def auto_verify_after_merge(runtime: Any) -> dict[str, Any] | None:
    """Run lint verification automatically after merge. Returns result or None."""
    commands = _resolve_commands(runtime.cwd, "lint", None)
    if not commands:
        return None
    try:
        results = await _run_checks_parallel(runtime, "lint", commands, timeout=120)
        overall = "pass" if all(r["status"] == "pass" for r in results) else "fail"
        return {"status": overall, "results": results}
    except Exception:
        console.print(f"  [yellow]![/yellow]  Auto-verify failed:\n{traceback.format_exc()}")
        return None


@tool(
    "merge_agent_branch",
    "Merge agent's branch back. Call after mark_task_done. Returns conflict info if merge fails.",
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
    branch = target.branch_name
    try:
        async with _get_merge_lock(integration):
            status, hash_, files, diff, commit_count = await anyio.to_thread.run_sync(
                lambda: _do_merge(runtime.cwd, branch, integration)
            )
    except (OSError, subprocess.TimeoutExpired) as e:
        return _merge_error_response(task_name, e)
    if status in ("merged", "no-op"):
        _defer_branch_cleanup(runtime, branch)
    return _tool_response(
        _merge_branch_result(
            task_name,
            status,
            hash_,
            files,
            diff,
            branch,
            integration,
            commit_count,
            cwd=runtime.cwd,
        )
    )
