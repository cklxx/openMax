"""Benchmark runner: execute tasks via Claude Code and openMax, collect metrics."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openmax.benchmark.tasks import BenchmarkTask

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Metrics from a single benchmark run."""

    task_id: str
    mode: str  # "claude-code" or "openmax"
    duration_seconds: float = 0.0
    success: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    num_subtasks: int = 0
    error: str | None = None


@dataclass
class TaskComparison:
    """Side-by-side comparison for one task."""

    task_id: str
    task_name: str
    difficulty: str
    claude_code: BenchmarkResult | None = None
    openmax: BenchmarkResult | None = None

    @property
    def speedup(self) -> float | None:
        if not self.claude_code or not self.openmax:
            return None
        if self.openmax.duration_seconds <= 0:
            return None
        return self.claude_code.duration_seconds / self.openmax.duration_seconds

    @property
    def cost_ratio(self) -> float | None:
        if not self.claude_code or not self.openmax:
            return None
        if self.claude_code.cost_usd <= 0:
            return None
        return self.openmax.cost_usd / self.claude_code.cost_usd


@dataclass
class BenchmarkReport:
    """Full benchmark report across all tasks."""

    comparisons: list[TaskComparison] = field(default_factory=list)
    model: str = ""
    timestamp: str = ""

    @property
    def avg_speedup(self) -> float | None:
        vals = [c.speedup for c in self.comparisons if c.speedup is not None]
        return sum(vals) / len(vals) if vals else None

    def to_dict(self) -> dict:
        return asdict(self)


def _trust_workspace(workspace: Path) -> None:
    """Pre-trust workspace for Claude Code so it skips the trust dialog."""
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {"permissions": {"allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]}}
    (claude_dir / "settings.local.json").write_text(
        json.dumps(settings, indent=2),
        encoding="utf-8",
    )


def _create_workspace(task: BenchmarkTask) -> Path:
    """Create an isolated temp workspace and run setup_script."""
    workspace = Path(tempfile.mkdtemp(prefix=f"bench-{task.id}-"))
    _trust_workspace(workspace)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=workspace,
        check=True,
        env=_git_env(),
    )
    if task.setup_script.strip():
        subprocess.run(
            ["bash", "-c", task.setup_script],
            cwd=workspace,
            check=True,
            timeout=60,
        )
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "setup", "-q"],
            cwd=workspace,
            check=True,
            env=_git_env(),
        )
    return workspace


def _git_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "benchmark")
    env.setdefault("GIT_AUTHOR_EMAIL", "bench@openmax")
    env.setdefault("GIT_COMMITTER_NAME", "benchmark")
    env.setdefault("GIT_COMMITTER_EMAIL", "bench@openmax")
    return env


def _clone_workspace(src: Path, label: str) -> Path:
    """Clone workspace to a new temp dir. Stays on main branch (no rename)."""
    dst = Path(tempfile.mkdtemp(prefix=f"bench-{label}-"))
    shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _verify(task: BenchmarkTask, workspace: Path) -> bool:
    """Run verify_script and check success_pattern."""
    try:
        result = subprocess.run(
            ["bash", "-c", task.verify_script],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = result.stdout + result.stderr
        return task.success_pattern in combined
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def _run_claude_code(
    task: BenchmarkTask,
    workspace: Path,
    model: str | None = None,
) -> BenchmarkResult:
    """Run task with Claude Code in print mode."""
    prompt = task.prompt.replace("{workspace}", str(workspace))
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd.extend(["--model", model])

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=task.timeout_seconds,
        )
        duration = time.monotonic() - t0
        success = _verify(task, workspace)
        tokens = _parse_claude_json_usage(proc.stdout)
        return BenchmarkResult(
            task_id=task.id,
            mode="claude-code",
            duration_seconds=round(duration, 2),
            success=success,
            **tokens,
        )
    except subprocess.TimeoutExpired:
        return BenchmarkResult(
            task_id=task.id,
            mode="claude-code",
            duration_seconds=task.timeout_seconds,
            error="timeout",
        )
    except Exception as exc:
        return BenchmarkResult(
            task_id=task.id,
            mode="claude-code",
            duration_seconds=time.monotonic() - t0,
            error=str(exc),
        )


def _parse_claude_json_usage(raw: str) -> dict:
    """Extract token/cost info from claude -p --output-format json output."""
    try:
        data = json.loads(raw)
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cost_usd": data.get("cost_usd", 0.0) if isinstance(data, dict) else 0.0,
        }
    except (json.JSONDecodeError, TypeError):
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _run_openmax(
    task: BenchmarkTask,
    workspace: Path,
    model: str | None = None,
) -> BenchmarkResult:
    """Run task with openMax multi-agent orchestration."""
    from openmax.lead_agent import run_lead_agent
    from openmax.pane_manager import PaneManager

    prompt = task.prompt.replace("{workspace}", str(workspace))
    session_id = f"bench-{task.id}-{int(time.time())}"

    pane_mgr = PaneManager()
    t0 = time.monotonic()
    try:
        plan = run_lead_agent(
            task=prompt,
            pane_mgr=pane_mgr,
            cwd=str(workspace),
            model=model,
            session_id=session_id,
            plan_confirm=False,
            allowed_agents=["claude-code"],
        )
        duration = time.monotonic() - t0
        success = _verify(task, workspace)

        total_tokens_in = sum(s.input_tokens for s in plan.subtasks)
        total_tokens_out = sum(s.output_tokens for s in plan.subtasks)
        total_cost = sum(s.cost_usd for s in plan.subtasks)

        return BenchmarkResult(
            task_id=task.id,
            mode="openmax",
            duration_seconds=round(duration, 2),
            success=success,
            input_tokens=total_tokens_in,
            output_tokens=total_tokens_out,
            cost_usd=round(total_cost, 4),
            num_subtasks=len(plan.subtasks),
        )
    except Exception as exc:
        return BenchmarkResult(
            task_id=task.id,
            mode="openmax",
            duration_seconds=time.monotonic() - t0,
            error=str(exc),
        )
    finally:
        try:
            pane_mgr.cleanup_all()
        except Exception:
            pass


def run_benchmark(
    tasks: list[BenchmarkTask],
    model: str | None = None,
    repeat: int = 1,
) -> BenchmarkReport:
    """Run all benchmark tasks, returning a comparison report."""
    from openmax._paths import utc_now_iso
    from openmax.output import console

    report = BenchmarkReport(model=model or "default", timestamp=utc_now_iso())

    for task in tasks:
        console.print(f"\n[bold]Task: {task.name}[/bold] ({task.difficulty})")

        for run_idx in range(repeat):
            if repeat > 1:
                console.print(f"  Run {run_idx + 1}/{repeat}")

            base_workspace = _create_workspace(task)
            cc_workspace: Path | None = None
            om_workspace: Path | None = None
            try:
                cc_workspace = _clone_workspace(base_workspace, "baseline")
                console.print("  [dim]Running Claude Code...[/dim]")
                cc_result = _run_claude_code(task, cc_workspace, model)
                _log_result("Claude Code", cc_result)

                om_workspace = _clone_workspace(base_workspace, "om")
                console.print("  [dim]Running openMax...[/dim]")
                om_result = _run_openmax(task, om_workspace, model)
                _log_result("openMax", om_result)

                comparison = TaskComparison(
                    task_id=task.id,
                    task_name=task.name,
                    difficulty=task.difficulty,
                    claude_code=cc_result,
                    openmax=om_result,
                )
                report.comparisons.append(comparison)
            finally:
                for d in (base_workspace, cc_workspace, om_workspace):
                    if d:
                        shutil.rmtree(d, ignore_errors=True)

    return report


def _log_result(label: str, result: BenchmarkResult) -> None:
    from openmax.output import console

    status = "[green]PASS[/green]" if result.success else "[red]FAIL[/red]"
    if result.error:
        status = f"[red]{result.error}[/red]"
    console.print(
        f"    {label}: {result.duration_seconds:.1f}s {status}"
        f" (${result.cost_usd:.4f}, {result.input_tokens + result.output_tokens:,} tokens)"
    )
