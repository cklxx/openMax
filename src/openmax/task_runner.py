"""Multi-task runner — execute multiple tasks concurrently via ThreadPoolExecutor."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from openmax.output import console
from openmax.project_registry import find_project, list_projects
from openmax.ui_coordinator import UICoordinator

_NUMBERED_RE = re.compile(r"^\s*\d+[\.\)]\s+", re.MULTILINE)
_SEPARATOR_RE = re.compile(r"^---+\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)
_MAX_AUTO_CONCURRENCY = 30


def split_multi_tasks(text: str) -> list[str]:
    """Extract independent tasks from a multi-task prompt via structural patterns."""
    tasks = _split_by_numbered_list(text)
    if len(tasks) > 1:
        return tasks
    tasks = _split_by_separator(text)
    if len(tasks) > 1:
        return tasks
    tasks = _split_by_headings(text)
    return tasks if len(tasks) > 1 else [text.strip()]


def _split_by_numbered_list(text: str) -> list[str]:
    """Split '1. foo\\n2. bar' into ['foo', 'bar']."""
    matches = list(_NUMBERED_RE.finditer(text))
    if len(matches) < 2:
        return []
    parts: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[m.end() : end].strip())
    return [p for p in parts if p]


def _split_by_separator(text: str) -> list[str]:
    """Split on '---' lines."""
    parts = _SEPARATOR_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_by_headings(text: str) -> list[str]:
    """Split on '## ' markdown headings, keeping heading text as task title."""
    matches = list(_HEADING_RE.finditer(text))
    if len(matches) < 2:
        return []
    parts: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[m.end() : end].strip())
    return [p for p in parts if p]


def confirm_tasks(tasks: list[str]) -> bool:
    """Display decomposed tasks and prompt user for confirmation."""
    console.print(f"\n  [bold cyan]Decomposed into {len(tasks)} tasks:[/bold cyan]")
    for i, t in enumerate(tasks, 1):
        label = t[:80] + "…" if len(t) > 80 else t
        console.print(f"    {i:>2}. [bold]{label}[/bold]")
    console.print()
    answer = console.input("  Run all in parallel? [Y/n]: ").strip().lower()
    return answer in ("", "y", "yes")


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
    concurrency: int = 0  # 0 = auto (min(len(tasks), 30))
    keep_panes: bool = False
    no_confirm: bool = True
    verbose: bool = False
    pane_backend_name: str = "auto"
    agents: list[str] | None = None
    on_progress: Any = None  # callback(task_idx, status, detail)
    direct: bool = True  # skip lead agent, run claude -p directly
    stagger_s: float = 1.0  # seconds between task launches


def route_task(prompt: str, projects: list[dict[str, str]]) -> str | None:
    """Match task prompt to a registered project by keyword. Returns cwd or None."""
    if not projects:
        return None
    prompt_lower = prompt.lower()
    for p in projects:
        if p["name"].lower() in prompt_lower:
            return p["path"]
    return None


def _run_single_task(
    idx: int,
    prompt: str,
    cwd: str,
    cfg: MultiTaskConfig,
    ui: UICoordinator,
) -> TaskResult:
    """Run one task in its own thread with its own lead agent session."""
    from openmax.agent_registry import built_in_agent_registry
    from openmax.lead_agent import LeadAgentStartupError, run_lead_agent
    from openmax.pane_manager import PaneManager

    result = TaskResult(task=prompt[:80], cwd=cwd, status="running")
    if cfg.on_progress:
        cfg.on_progress(idx, "running", prompt[:60])

    t0 = time.monotonic()
    try:
        pane_mgr = PaneManager(backend_name=cfg.pane_backend_name)
        session_id = f"multi-{idx}-{int(time.time())}"
        plan = run_lead_agent(
            task=prompt,
            pane_mgr=pane_mgr,
            cwd=cwd,
            model=cfg.model,
            max_turns=cfg.max_turns,
            session_id=session_id,
            allowed_agents=cfg.agents,
            agent_registry=built_in_agent_registry(),
            plan_confirm=not cfg.no_confirm,
            verbose=cfg.verbose,
            ui_coordinator=ui,
        )
        result.status = "done"
        result.subtask_count = len(plan.subtasks)
        if not cfg.keep_panes:
            try:
                pane_mgr.cleanup_all()
            except Exception:
                pass
    except LeadAgentStartupError as exc:
        result.status = "failed"
        result.error = str(exc)
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)

    result.duration_s = round(time.monotonic() - t0, 1)
    if cfg.on_progress:
        cfg.on_progress(idx, result.status, result.error or "")
    return result


def _run_direct_task(idx: int, prompt: str, cwd: str, cfg: MultiTaskConfig) -> TaskResult:
    """Run one task directly via 'claude -p' without lead agent overhead."""
    result = TaskResult(task=prompt[:80], cwd=cwd, status="running")
    if cfg.on_progress:
        cfg.on_progress(idx, "running", prompt[:60])
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        result.status = "done" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            result.error = (proc.stderr or proc.stdout)[:200]
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
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


def _resolve_concurrency(cfg: MultiTaskConfig) -> int:
    if cfg.concurrency > 0:
        return min(cfg.concurrency, len(cfg.tasks))
    return min(len(cfg.tasks), _MAX_AUTO_CONCURRENCY)


def _submit_staggered(
    pool: ThreadPoolExecutor,
    cfg: MultiTaskConfig,
    ui: UICoordinator,
) -> dict[Future[TaskResult], int]:
    """Submit tasks with staggered delays to avoid API rate limit spikes."""
    futures: dict[Future[TaskResult], int] = {}
    for i, (prompt, cwd) in enumerate(cfg.tasks):
        if cfg.direct:
            fut = pool.submit(_run_direct_task, i, prompt, cwd, cfg)
        else:
            fut = pool.submit(_run_single_task, i, prompt, cwd, cfg, ui)
        futures[fut] = i
        if i < len(cfg.tasks) - 1 and cfg.stagger_s > 0:
            time.sleep(cfg.stagger_s)
    return futures


def _collect_results(
    futures: dict[Future[TaskResult], int],
    tasks: list[tuple[str, str]],
) -> list[TaskResult]:
    """Gather results from completed futures in submission order."""
    results: list[TaskResult | None] = [None] * len(tasks)
    for future in as_completed(futures):
        idx = futures[future]
        try:
            results[idx] = future.result()
        except Exception as exc:
            results[idx] = TaskResult(
                task=tasks[idx][0][:80],
                cwd=tasks[idx][1],
                status="failed",
                error=str(exc),
            )
    return [r for r in results if r is not None]


def run_tasks(cfg: MultiTaskConfig) -> list[TaskResult]:
    """Run multiple tasks concurrently. Returns results in submission order."""
    if not cfg.tasks:
        return []
    max_workers = _resolve_concurrency(cfg)
    mode = "direct" if cfg.direct else "lead-agent"
    ui = UICoordinator(tasks=[prompt for prompt, _ in cfg.tasks])
    ui.print_banner(f"batch-{int(time.time())}")
    console.print(
        f"  [bold]Running {len(cfg.tasks)} tasks[/bold] (concurrency={max_workers}, mode={mode})"
    )
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = _submit_staggered(pool, cfg, ui)
        final = _collect_results(futures, cfg.tasks)
    _print_summary(final)
    _notify_completion(final)
    return final


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
            console.print(f"      [red]{r.error[:100]}[/red]")


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
