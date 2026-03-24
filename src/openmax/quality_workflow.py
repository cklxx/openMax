"""Quality workflow — sequential write → review → rewrite with React loop agents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openmax.output import console


@dataclass
class WorkflowStep:
    """One step in the quality workflow."""

    role: str  # writer | reviewer | challenger | writer (rewrite)
    prompt_template: str  # {task}, {files}, {feedback} placeholders


QUALITY_STEPS: list[WorkflowStep] = [
    WorkflowStep(
        role="writer",
        prompt_template="{task}\n\nRun tests and commit your changes when done.",
    ),
    WorkflowStep(
        role="reviewer",
        prompt_template=(
            "Review the code changes for task: {task}\n\n"
            "Read all changed files. Find:\n"
            "- DRY violations (duplicated logic)\n"
            "- Functions >15 lines (need extraction)\n"
            "- Missing abstractions (repeated patterns)\n"
            "- Over-engineering (abstractions used <2 places)\n\n"
            "Rate 1-10: DRY, readability, architecture.\n"
            "Write structured findings to .openmax/reports/{name}-review.md"
        ),
    ),
    WorkflowStep(
        role="writer",
        prompt_template=(
            "REWRITE the code for task: {task}\n\n"
            "Reviewer feedback:\n{feedback}\n\n"
            "Goal: SHORTER code, better abstractions, zero duplication. "
            "Every function ≤15 lines. Composition over inheritance. "
            "Delete unnecessary abstractions. Commit when done."
        ),
    ),
]


def _read_report(cwd: str, name: str) -> str:
    """Read the reviewer report for a task."""
    report = Path(cwd) / ".openmax" / "reports" / f"{name}-review.md"
    if report.exists():
        return report.read_text(encoding="utf-8")[:3000]
    return "No review report found. Improve: DRY, shorter functions, better abstractions."


async def run_quality_workflow(
    runtime: Any,
    task_name: str,
    task_prompt: str,
) -> list[dict[str, Any]]:
    """Execute write → review → rewrite workflow for one task.

    Each step dispatches an agent (React loop internally) and waits for completion.
    The output of each step feeds into the next.
    """
    from openmax.lead_agent.tools._dispatch import dispatch_agent
    from openmax.lead_agent.tools._misc import _monitor_until_done
    from openmax.lead_agent.tools._planning import mark_task_done
    from openmax.lead_agent.tools._verify import merge_agent_branch

    step_results: list[dict[str, Any]] = []
    feedback = ""

    for i, step in enumerate(QUALITY_STEPS):
        step_label = f"{step.role}" if i != 2 else "rewrite"
        suffix = f"-{step_label}" if i > 0 else ""
        agent_name = f"{task_name}{suffix}"

        prompt = step.prompt_template.format(
            task=task_prompt,
            name=task_name,
            feedback=feedback,
            files="",
        )

        console.print(
            f"  [bold magenta]Q[/bold magenta]  "
            f"step {i + 1}/{len(QUALITY_STEPS)}: {step_label} → {agent_name}"
        )

        t0 = time.monotonic()
        await dispatch_agent.handler(
            {
                "task_name": agent_name,
                "role": step.role,
                "prompt": prompt,
            }
        )

        # Wait for this one agent to finish (React loop runs inside)
        results, _ = await _monitor_until_done(runtime, timeout=600)
        duration = round(time.monotonic() - t0, 1)

        # Collect output for next step
        if step.role == "reviewer":
            feedback = _read_report(runtime.cwd, task_name)

        # Mark done + merge for each step
        for r in results:
            if r.get("type") == "done":
                try:
                    await mark_task_done.handler(
                        {
                            "task_name": r["task"],
                            "notes": "",
                        }
                    )
                    await merge_agent_branch.handler(
                        {
                            "task_name": r["task"],
                        }
                    )
                except Exception:
                    pass

        step_results.append(
            {
                "step": step_label,
                "agent": agent_name,
                "duration_s": duration,
                "messages": len(results),
            }
        )

        console.print(f"  [bold magenta]Q[/bold magenta]  step {i + 1} done ({duration}s)")

    return step_results
