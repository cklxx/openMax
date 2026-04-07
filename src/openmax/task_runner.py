"""Multi-task runner module."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from openmax.output import console
from openmax.project_registry import find_project, list_projects
from openmax.ui_coordinator import UICoordinator

logger = logging.getLogger(__name__)

_LLM_MIN_LENGTH = 80  # only attempt LLM split for prompts longer than this

_DECOMPOSE_SYSTEM = (
    "You decompose a user request into independent tasks. "
    "Return a JSON array of task strings. Each task should be a self-contained instruction. "
    "If the input is already a single coherent task, return a single-element array. "
    'Example: ["Fix login bug in auth.py", "Add pagination to /users endpoint"]'
)


def split_multi_tasks(text: str) -> list[str]:
    """Extract independent tasks from a multi-task prompt via LLM decomposition."""
    if len(text) > _LLM_MIN_LENGTH:
        tasks = _split_via_llm(text)
        if len(tasks) > 1:
            return tasks
    return [text.strip()]


def _split_via_llm(text: str) -> list[str]:
    """Use claude CLI to decompose unstructured text into independent tasks."""
    prompt = f"{_DECOMPOSE_SYSTEM}\n\nUser request:\n{text}"
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-sonnet-4-6"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return []
        raw = proc.stdout.strip()
        # Extract JSON array from response (may have markdown fences)
        return _parse_json_tasks(raw)
    except Exception as exc:
        logger.debug("LLM task decomposition failed: %s", exc)
    return []


def _parse_json_tasks(raw: str) -> list[str]:
    """Extract a JSON string array from LLM output, handling markdown fences."""
    # Try direct parse first
    try:
        tasks = json.loads(raw)
        if isinstance(tasks, list) and all(isinstance(t, str) for t in tasks):
            return [t.strip() for t in tasks if t.strip()]
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code fence
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if match:
        try:
            tasks = json.loads(match.group(1))
            if isinstance(tasks, list) and all(isinstance(t, str) for t in tasks):
                return [t.strip() for t in tasks if t.strip()]
        except json.JSONDecodeError:
            pass
    return []


def confirm_tasks(tasks: list[str]) -> bool:
    """Display decomposed tasks and prompt user for confirmation."""
    console.print(f"\n  [bold cyan]Decomposed into {len(tasks)} tasks:[/bold cyan]")
    for i, t in enumerate(tasks, 1):
        label = t[:80] + "…" if len(t) > 80 else t
        console.print(f"    {i:>2}. [bold]{label}[/bold]")
    console.print()
    answer = console.input("  Run all in parallel? [Y/n]: ").strip().lower()
    return answer in ("", "y", "yes")


def format_batch_prompt(tasks: tuple[str, ...] | list[str]) -> str:
    """Format multiple tasks into a structured prompt for the lead agent."""
    lines = [
        f"Execute the following {len(tasks)} INDEPENDENT tasks in parallel.",
        "Each task should be dispatched as a separate sub-agent.",
        "All tasks are independent — no dependencies between them.\n",
    ]
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. {t}")
    return "\n".join(lines)


@dataclass
class TaskResult:
    """Result from a single task execution."""

    task: str
    cwd: str
    status: str = "pending"  # pending | running | done | failed
    duration_s: float = 0.0
    error: str | None = None
    subtask_count: int = 0


@dataclass
class MultiTaskConfig:
    """Configuration for a multi-task run."""

    tasks: list[tuple[str, str]]  # [(prompt, cwd), ...]
    model: str | None = None
    max_turns: int | None = None
    concurrency: int = 6
    keep_panes: bool = False
    no_confirm: bool = True
    verbose: bool = False
    pane_backend_name: str = "auto"
    agents: list[str] | None = None
    on_progress: Any = None  # callback(task_idx, status, detail)


def route_task(prompt: str, projects: list[dict[str, str]]) -> str | None:
    """Match task prompt to a registered project by keyword. Returns cwd or None."""
    if not projects:
        return None
    prompt_lower = prompt.lower()
    for p in projects:
        if p["name"].lower() in prompt_lower:
            return p["path"]
    return None


def _execute_task(idx: int, prompt: str, cwd: str, cfg: MultiTaskConfig, ui: UICoordinator) -> Any:
    """Create a lead agent session and run one task. Returns PlanResult."""
    from openmax.agent_registry import built_in_agent_registry
    from openmax.lead_agent import run_lead_agent
    from openmax.pane_manager import PaneManager

    pane_mgr = PaneManager(backend_name=cfg.pane_backend_name)
    plan = run_lead_agent(
        task=prompt,
        pane_mgr=pane_mgr,
        cwd=cwd,
        model=cfg.model,
        max_turns=cfg.max_turns,
        session_id=f"multi-{idx}-{int(time.time())}",
        allowed_agents=cfg.agents,
        agent_registry=built_in_agent_registry(),
        plan_confirm=not cfg.no_confirm,
        verbose=cfg.verbose,
        ui_coordinator=ui,
    )
    if not cfg.keep_panes:
        try:
            pane_mgr.cleanup_all()
        except Exception:
            pass
    return plan


def _run_single_task(
    idx: int, prompt: str, cwd: str, cfg: MultiTaskConfig, ui: UICoordinator
) -> TaskResult:
    """Run one task in its own thread with timing and error handling."""
    result = TaskResult(task=prompt[:80], cwd=cwd, status="running")
    if cfg.on_progress:
        cfg.on_progress(idx, "running", prompt[:60])
    t0 = time.monotonic()
    try:
        plan = _execute_task(idx, prompt, cwd, cfg, ui)
        result.status = "done"
        result.subtask_count = len(plan.subtasks)
    except Exception:
        result.status = "failed"
        result.error = traceback.format_exc()
    result.duration_s = round(time.monotonic() - t0, 1)
    if cfg.on_progress:
        cfg.on_progress(idx, result.status, result.error or "")
    return result


def resolve_task_cwds(
    tasks: tuple[str, ...],
    projects: tuple[str, ...],
    default_cwd: str,
) -> list[tuple[str, str]]:
    """Resolve each task to a (prompt, cwd) pair."""
    registered = list_projects()
    result: list[tuple[str, str]] = []

    for i, task in enumerate(tasks):
        if i < len(projects):
            cwd = find_project(projects[i]) or projects[i]
        else:
            routed = route_task(task, registered)
            cwd = routed or default_cwd
        result.append((task, cwd))
    return result


def _run_concurrent(cfg: MultiTaskConfig, ui: UICoordinator) -> list[TaskResult]:
    """Execute tasks in a thread pool and collect results."""
    results: list[TaskResult | None] = [None] * len(cfg.tasks)
    max_workers = min(cfg.concurrency, len(cfg.tasks))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_single_task, i, prompt, cwd, cfg, ui): i
            for i, (prompt, cwd) in enumerate(cfg.tasks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = TaskResult(
                    task=cfg.tasks[idx][0][:80],
                    cwd=cfg.tasks[idx][1],
                    status="failed",
                    error=str(exc),
                )
    return [r for r in results if r is not None]


def run_tasks(cfg: MultiTaskConfig) -> list[TaskResult]:
    """Run multiple tasks concurrently. Returns results in submission order."""
    if not cfg.tasks:
        return []
    ui = UICoordinator(tasks=[prompt for prompt, _ in cfg.tasks])
    ui.print_banner(f"batch-{int(time.time())}")
    n = min(cfg.concurrency, len(cfg.tasks))
    console.print(f"  [bold]Running {len(cfg.tasks)} tasks[/bold] (concurrency={n})")
    results = _run_concurrent(cfg, ui)
    _print_summary(results)
    _notify_completion(results)
    return results


def _print_summary(results: list[TaskResult]) -> None:
    """Print a batch summary of all task results."""
    done = sum(1 for r in results if r.status == "done")
    failed = sum(1 for r in results if r.status == "failed")
    total_time = max((r.duration_s for r in results), default=0)

    console.print(
        f"\n  [bold]Batch complete:[/bold] {done} done, {failed} failed, {total_time:.0f}s"
    )
    for r in results:
        icon = "[green]✓[/green]" if r.status == "done" else "[red]✗[/red]"
        console.print(f"    {icon} {r.task} ({r.duration_s:.0f}s)")
        if r.error:
            for line in r.error.rstrip().splitlines():
                console.print(f"      [red]{line}[/red]")


def _notify_completion(results: list[TaskResult]) -> None:
    """Send macOS notification on completion."""
    if sys.platform != "darwin":
        return
    done = sum(1 for r in results if r.status == "done")
    failed = sum(1 for r in results if r.status == "failed")
    msg = f"{done}/{len(results)} tasks completed"
    if failed:
        msg += f", {failed} failed"
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "openMax"'],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
