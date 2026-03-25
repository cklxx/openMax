"""Quality workflow — write → review → challenge → rewrite with AST enforcement."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openmax.output import console


@dataclass
class WorkflowStep:
    """One step in the quality workflow."""

    role: str
    prompt_template: str
    step_type: str  # "write" | "review" | "challenge" | "rewrite"
    commits: bool = True


QUALITY_STEPS: list[WorkflowStep] = [
    WorkflowStep(
        role="writer",
        step_type="write",
        commits=True,
        prompt_template="{task}\n\nRun tests and commit your changes when done.",
    ),
    WorkflowStep(
        role="reviewer",
        step_type="review",
        commits=False,
        prompt_template=(
            "Review the code changes for task: {task}\n\n"
            "Read all changed files. Analyze code quality:\n"
            "{violations_block}\n"
            "Write structured findings to .openmax/reports/{name}-review.md"
        ),
    ),
    WorkflowStep(
        role="challenger",
        step_type="challenge",
        commits=False,
        prompt_template=(
            "Challenge the code for task: {task}\n\n"
            "Read all changed files. Propose a RADICALLY SIMPLER alternative:\n"
            "- Write pseudocode of a version that's 50% less code\n"
            "- Question every class and abstraction\n"
            "- Identify specific merges and simplifications\n\n"
            "Write your counter-design to .openmax/reports/{name}-challenge.md"
        ),
    ),
    WorkflowStep(
        role="writer",
        step_type="rewrite",
        commits=True,
        prompt_template=(
            "REWRITE the code for task: {task}\n\n"
            "## Reviewer findings:\n{feedback}\n\n"
            "## Challenger counter-design:\n{challenge_feedback}\n\n"
            "{violations_block}\n"
            "## Objectives (in priority order):\n"
            "1. MANDATORY: Fix all AST violations listed above\n"
            "2. MANDATORY: Apply reviewer critical/major findings\n"
            "3. CONDITIONAL: Adopt challenger design where it reduces code\n"
            "4. ASPIRATIONAL: Further reduction if achievable\n"
            "Commit when done."
        ),
    ),
]


def _read_report(cwd: str, name: str) -> str:
    """Read the reviewer report for a task."""
    report = Path(cwd) / ".openmax" / "reports" / f"{name}-review.md"
    if report.exists():
        return report.read_text(encoding="utf-8")[:3000]
    return "No review report found."


def _read_challenge_report(cwd: str, name: str) -> str:
    """Read the challenger report for a task."""
    report = Path(cwd) / ".openmax" / "reports" / f"{name}-challenge.md"
    if report.exists():
        return report.read_text(encoding="utf-8")[:3000]
    return "No challenge report found."


def _persist_report(runtime: Any, task_name: str, suffix: str) -> None:
    """Copy a report from the agent's worktree to main cwd."""
    rt = runtime
    for st in rt.plan.subtasks:
        if st.name != task_name or not st.branch_name:
            continue
        wt = Path(rt.cwd) / ".openmax-worktrees" / st.branch_name.replace("/", "_")
        src = wt / ".openmax" / "reports" / f"{task_name.rsplit('-', 1)[0]}-{suffix}.md"
        if not src.exists():
            continue
        dst = Path(rt.cwd) / ".openmax" / "reports" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _run_ast_check(cwd: str) -> tuple[list[Any], str]:
    """Run AST style check on recently changed Python files."""
    from openmax.style_check import check_style_violations, format_violations

    try:
        import subprocess

        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "--", "*.py"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = [str(Path(cwd) / f.strip()) for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        files = []
    if not files:
        return [], ""
    violations = check_style_violations(files)
    return violations, format_violations(violations)


async def run_quality_workflow(
    runtime: Any,
    task_name: str,
    task_prompt: str,
) -> list[dict[str, Any]]:
    """Execute write → review → challenge → rewrite with AST enforcement."""
    from openmax.lead_agent.tools._dispatch import dispatch_agent
    from openmax.lead_agent.tools._misc import _monitor_until_done
    from openmax.lead_agent.tools._planning import mark_task_done
    from openmax.lead_agent.tools._verify import merge_agent_branch

    step_results: list[dict[str, Any]] = []
    feedback = ""
    challenge_feedback = ""
    violations_block = ""
    violation_count_start = 0

    total = len(QUALITY_STEPS)
    for i, step in enumerate(QUALITY_STEPS):
        suffix = f"-{step.step_type}" if i > 0 else ""
        agent_name = f"{task_name}{suffix}"

        prompt = step.prompt_template.format(
            task=task_prompt,
            name=task_name,
            feedback=feedback,
            challenge_feedback=challenge_feedback,
            violations_block=violations_block,
            files="",
        )

        console.print(
            f"  [bold magenta]Q[/bold magenta]  "
            f"step {i + 1}/{total}: {step.step_type} → {agent_name}"
        )

        t0 = time.monotonic()
        await dispatch_agent.handler({"task_name": agent_name, "role": step.role, "prompt": prompt})
        results, _ = await _monitor_until_done(runtime, timeout=600)
        duration = round(time.monotonic() - t0, 1)

        step_result: dict[str, Any] = {
            "step": step.step_type,
            "agent": agent_name,
            "duration_s": duration,
            "messages": len(results),
        }

        for r in results:
            if r.get("type") != "done":
                continue
            try:
                await mark_task_done.handler({"task_name": r["task"], "notes": ""})
                if step.commits:
                    await merge_agent_branch.handler({"task_name": r["task"]})
            except Exception:
                pass

        # Post-step hooks
        if step.step_type == "write":
            violations, violations_block = _run_ast_check(runtime.cwd)
            violation_count_start = len(violations)
            step_result["violation_count"] = violation_count_start
            if not violations_block:
                violations_block = ""

        if step.step_type == "review":
            _persist_report(runtime, agent_name, "review")
            feedback = _read_report(runtime.cwd, task_name)

        if step.step_type == "challenge":
            _persist_report(runtime, agent_name, "challenge")
            challenge_feedback = _read_challenge_report(runtime.cwd, task_name)

        if step.step_type == "rewrite":
            violations_end, _ = _run_ast_check(runtime.cwd)
            step_result["violation_count"] = len(violations_end)
            step_result["violation_delta"] = len(violations_end) - violation_count_start

        step_results.append(step_result)
        console.print(f"  [bold magenta]Q[/bold magenta]  step {i + 1} done ({duration}s)")

    return step_results
