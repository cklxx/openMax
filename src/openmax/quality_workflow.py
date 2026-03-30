"""Quality workflow — write → review → challenge → rewrite with AST enforcement.

Also implements the harness workflow: planner → generator ↔ evaluator loop.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openmax.lead_agent.types import TaskStatus
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


# ---------------------------------------------------------------------------
# Harness workflow: planner → generator ↔ evaluator loop
# ---------------------------------------------------------------------------

MAX_HARNESS_ROUNDS = 5

FRONTEND_DIMENSIONS: dict[str, dict[str, float]] = {
    "design_quality": {"weight": 0.35, "threshold": 7},
    "originality": {"weight": 0.30, "threshold": 7},
    "craftsmanship": {"weight": 0.20, "threshold": 6},
    "functionality": {"weight": 0.15, "threshold": 6},
}

FULLSTACK_DIMENSIONS: dict[str, dict[str, float]] = {
    "product_depth": {"weight": 0.30, "threshold": 7},
    "functionality": {"weight": 0.30, "threshold": 7},
    "design_quality": {"weight": 0.25, "threshold": 6},
    "code_quality": {"weight": 0.15, "threshold": 6},
}

# Default — overridden by archetype detection in run_harness_workflow
EVAL_DIMENSIONS: dict[str, dict[str, float]] = FRONTEND_DIMENSIONS

_H = "[bold yellow]H[/bold yellow]"


def _persist_from_worktree(
    runtime: Any,
    task_name: str,
    src_subdir: str,
    src_filename: str,
    dst_subdir: str | None = None,
    dst_filename: str | None = None,
) -> str:
    """Copy a .openmax/ file from agent worktree to main cwd. Returns content or ''."""
    for st in runtime.plan.subtasks:
        if st.name != task_name or not st.branch_name:
            continue
        wt = Path(runtime.cwd) / ".openmax-worktrees" / st.branch_name.replace("/", "_")
        src = wt / ".openmax" / src_subdir / src_filename
        if not src.exists():
            continue
        content = src.read_text(encoding="utf-8")
        dst_dir = dst_subdir or src_subdir
        dst_name = dst_filename or src_filename
        dst = Path(runtime.cwd) / ".openmax" / dst_dir / dst_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        return content
    return ""


def _build_planner_prompt(task_prompt: str) -> str:
    return (
        f"Create a product spec for: {task_prompt}\n\n"
        "## Constraints\n"
        "- Be BOLD in scope — include AI-powered features where sensible\n"
        "- Define: user stories, data model, design language, interaction patterns\n"
        "- Do NOT include implementation details (they cascade errors)\n"
        "- Write the full spec to `.openmax/specs/spec.md`\n"
        "- Then report done."
    )


def _build_generator_prompt(
    task_prompt: str,
    spec: str,
    contract: str,
    prev_eval: str,
) -> str:
    parts = [task_prompt, f"## Product Spec\n{spec[:3000]}", f"## Sprint Contract\n{contract}"]
    if prev_eval:
        parts.append(f"## Previous Evaluation (fix these issues)\n{prev_eval[:3000]}")
    parts.append("Run tests and commit your changes when done.")
    return "\n\n".join(parts)


def _build_evaluator_prompt(
    task_name: str,
    round_num: int,
    dims: dict[str, dict[str, float]] | None = None,
) -> str:
    dims = dims or EVAL_DIMENSIONS
    dims_text = "\n".join(
        f"- {name} (weight {d['weight']:.0%}, threshold {d['threshold']}/10)"
        for name, d in dims.items()
    )
    return (
        f"Evaluate task '{task_name}' (round {round_num}).\n\n"
        "1. Read the project to find how to start the dev server\n"
        "2. Start the dev server (or detect if running)\n"
        "3. Use browser/browse tools to navigate and interact with the live app\n"
        "4. Score each dimension 0-10 with detailed justification\n\n"
        f"## Dimensions\n{dims_text}\n\n"
        f"Write evaluation to `.openmax/evaluations/{task_name}-round-{round_num}.md`.\n"
        "Format: `## <Dimension>\\nScore: N/10\\n<justification>\\n"
        "Improvements: <specific actions>`\n\n"
        "Then report done."
    )


def _build_contract(task_prompt: str, spec: str, round_num: int, prev_eval: str) -> str:
    parts = [
        f"# Sprint Contract — Round {round_num}\n",
        f"## Goal\n{task_prompt}\n",
        "## Acceptance Criteria (from spec)\n",
        spec[:1500] if spec else "(no spec available)",
    ]
    if prev_eval:
        parts.append(f"\n## Must Fix (from evaluation)\n{prev_eval[:1500]}")
    parts.append("\n## Rules\n- All acceptance criteria must pass\n- Run tests before committing")
    return "\n".join(parts)


_SCORE_PATTERN = re.compile(r"Score:\s*(\d+)\s*/\s*10", re.IGNORECASE)


def _normalize_dimension_key(
    heading: str,
    dims: dict[str, dict[str, float]] | None = None,
) -> str | None:
    """Fuzzy-match a heading to a dimension key."""
    dims = dims or EVAL_DIMENSIONS
    # Strip leading numbers like "1. " or "1) " BEFORE lowercasing
    cleaned = re.sub(r"^\d+[.)]\s*", "", heading.strip())
    key = cleaned.lower().replace(" ", "_")
    if key in dims:
        return key
    # Partial match: "design" → "design_quality", "original" → "originality"
    for dim in dims:
        if key.startswith(dim.split("_")[0]):
            return dim
    return None


def _parse_evaluation(
    cwd: str,
    task_name: str,
    round_num: int,
    dims: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Parse evaluation file into {dimension: score} dict."""
    from openmax.task_file import read_evaluation

    dims = dims or EVAL_DIMENSIONS
    text = read_evaluation(cwd, task_name, round_num)
    if not text:
        return {}
    scores: dict[str, float] = {}
    current_dim: str | None = None
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            heading = line.lstrip("#").strip()
            current_dim = _normalize_dimension_key(heading, dims)
        if current_dim and (m := _SCORE_PATTERN.search(line)):
            scores[current_dim] = float(m.group(1))
            current_dim = None
    return scores


def _all_above_threshold(
    scores: dict[str, float],
    dims: dict[str, dict[str, float]] | None = None,
) -> bool:
    dims = dims or EVAL_DIMENSIONS
    return all(scores.get(dim, 0) >= info["threshold"] for dim, info in dims.items())


def _weighted_average(
    scores: dict[str, float],
    dims: dict[str, dict[str, float]] | None = None,
) -> float:
    dims = dims or EVAL_DIMENSIONS
    total = sum(scores.get(d, 0) * info["weight"] for d, info in dims.items())
    return round(total, 2)


def _decide_next(
    scores: dict[str, float],
    round_num: int,
    history: list[dict[str, float]],
    dims: dict[str, dict[str, float]] | None = None,
) -> str:
    if _all_above_threshold(scores, dims):
        return "accept"
    if round_num >= MAX_HARNESS_ROUNDS:
        return "accept"
    if len(history) >= 2:
        prev_avg = _weighted_average(history[-2], dims)
        curr_avg = _weighted_average(scores, dims)
        if curr_avg < prev_avg - 1.0:
            return "pivot"
    return "refine"


def _record_metrics(
    runtime: Any,
    task_name: str,
    round_num: int,
    scores: dict[str, float],
    duration: float,
    dims: dict[str, dict[str, float]] | None = None,
) -> None:
    from openmax.lead_agent.tools._helpers import _append_session_event

    _append_session_event(
        "harness.evaluation",
        {
            "task": task_name,
            "round": round_num,
            "scores": scores,
            "weighted_avg": _weighted_average(scores, dims),
            "pass": _all_above_threshold(scores, dims),
            "duration_s": round(duration, 1),
        },
    )


def _detect_quality_peak(
    history: list[dict[str, float]],
    dims: dict[str, dict[str, float]] | None = None,
) -> int:
    """Return 1-indexed round number with the best weighted average."""
    if not history:
        return 1
    avgs = [_weighted_average(s, dims) for s in history]
    return avgs.index(max(avgs)) + 1


async def _dispatch_and_wait(
    runtime: Any,
    agent_name: str,
    role: str,
    prompt: str,
) -> list[dict[str, Any]]:
    """Dispatch an agent and block until it completes.

    _monitor_until_done handles done messages via _handle_message,
    which calls _auto_mark_and_merge (mark_done + merge) automatically.
    No additional mark/merge needed after this returns.
    """
    from openmax.lead_agent.tools._dispatch import dispatch_agent
    from openmax.lead_agent.tools._misc import _monitor_until_done

    await dispatch_agent.handler({"task_name": agent_name, "role": role, "prompt": prompt})
    results, _ = await _monitor_until_done(runtime, timeout=600)
    # If agent exited without MCP callback, force-mark done
    await _force_mark_if_still_running(runtime, agent_name)
    return results


async def _force_mark_if_still_running(runtime: Any, task_name: str) -> None:
    """Safety net: mark task done if monitor timed out but pane exited."""
    from openmax.lead_agent.tools._planning import mark_task_done

    for st in runtime.plan.subtasks:
        if st.name == task_name and st.status == TaskStatus.RUNNING:
            alive = runtime.pane_mgr.is_pane_alive(st.pane_id) if st.pane_id else False
            if not alive:
                await mark_task_done.handler({"task_name": task_name, "notes": "force-marked"})
                console.print(f"  {_H}  [yellow]force-marked {task_name} done[/yellow]")
            break


async def _run_planner_phase(
    runtime: Any,
    task_name: str,
    task_prompt: str,
) -> str:
    """Dispatch planner agent, wait, persist spec from worktree."""
    from openmax.lead_agent.tools._helpers import _append_session_event
    from openmax.task_file import read_spec

    console.print(f"  {_H}  phase: planner → spec")
    t0 = time.monotonic()
    prompt = _build_planner_prompt(task_prompt)
    agent_name = f"{task_name}-planner"
    await _dispatch_and_wait(runtime, agent_name, "planner", prompt)
    # Copy spec from worktree to main cwd (gitignored files don't survive merge)
    _persist_from_worktree(runtime, agent_name, "specs", "spec.md")
    duration = round(time.monotonic() - t0, 1)
    spec = read_spec(runtime.cwd) or ""
    if not spec:
        console.print(f"  {_H}  [yellow]warning: planner produced empty spec[/yellow]")
    _append_session_event(
        "harness.planner_done",
        {
            "task": task_name,
            "spec_chars": len(spec),
            "duration_s": duration,
        },
    )
    console.print(f"  {_H}  planner done ({duration}s, {len(spec)} chars)")
    return spec


async def _run_generator_phase(
    runtime: Any,
    task_name: str,
    task_prompt: str,
    spec: str,
    contract: str,
    prev_eval: str,
    round_num: int,
) -> float:
    """Dispatch generator, wait, merge. Returns duration."""
    from openmax.lead_agent.tools._helpers import _append_session_event

    console.print(f"  {_H}  round {round_num}: generator")
    t0 = time.monotonic()
    prompt = _build_generator_prompt(task_prompt, spec, contract, prev_eval)
    agent_name = f"{task_name}-gen-r{round_num}"
    await _dispatch_and_wait(runtime, agent_name, "writer", prompt)
    duration = round(time.monotonic() - t0, 1)
    _append_session_event(
        "harness.generator_done",
        {
            "task": task_name,
            "round": round_num,
            "duration_s": duration,
        },
    )
    console.print(f"  {_H}  generator done ({duration}s)")
    return duration


async def _run_evaluator_phase(
    runtime: Any,
    task_name: str,
    round_num: int,
    dims: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, float], float]:
    """Dispatch evaluator, wait, persist evaluation, parse scores."""
    console.print(f"  {_H}  round {round_num}: evaluator")
    t0 = time.monotonic()
    prompt = _build_evaluator_prompt(task_name, round_num, dims)
    agent_name = f"{task_name}-eval-r{round_num}"
    await _dispatch_and_wait(runtime, agent_name, "evaluator", prompt)
    # Copy evaluation from worktree to main cwd
    eval_file = f"{task_name}-round-{round_num}.md"
    _persist_from_worktree(runtime, agent_name, "evaluations", eval_file)
    duration = round(time.monotonic() - t0, 1)
    scores = _parse_evaluation(runtime.cwd, task_name, round_num, dims)
    if not scores:
        console.print(f"  {_H}  [yellow]warning: evaluator returned no parseable scores[/yellow]")
    avg = _weighted_average(scores, dims)
    passed = _all_above_threshold(scores, dims)
    tag = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
    console.print(f"  {_H}  evaluator done ({duration}s) avg={avg} {tag}")
    return scores, duration


def _select_dimensions(runtime: Any) -> dict[str, dict[str, float]]:
    """Pick evaluation dimensions based on matched archetype."""
    arch = getattr(runtime, "matched_archetype", None)
    arch_name = getattr(arch, "name", "") if arch else ""
    if arch_name in ("api_service", "cli_tool", "library"):
        return FULLSTACK_DIMENSIONS
    return FRONTEND_DIMENSIONS


@dataclass
class _HarnessState:
    """Mutable state passed through harness round iterations."""

    dims: dict[str, dict[str, float]]
    spec: str
    history: list[dict[str, float]] = field(default_factory=list)
    prev_eval_text: str = ""
    step_results: list[dict[str, Any]] = field(default_factory=list)


async def _run_harness_round(
    runtime: Any,
    task_name: str,
    task_prompt: str,
    round_num: int,
    state: _HarnessState,
) -> str:
    """Execute one generate → evaluate round. Returns decision action."""
    from openmax.lead_agent.tools._helpers import _append_session_event
    from openmax.task_file import write_contract

    contract = _build_contract(task_prompt, state.spec, round_num, state.prev_eval_text)
    write_contract(runtime.cwd, task_name, round_num, contract)
    _append_session_event("harness.contract", {"task": task_name, "round": round_num})

    gen_dur = await _run_generator_phase(
        runtime,
        task_name,
        task_prompt,
        state.spec,
        contract,
        state.prev_eval_text,
        round_num,
    )
    scores, eval_dur = await _run_evaluator_phase(runtime, task_name, round_num, state.dims)
    state.history.append(scores)
    runtime.harness_scores.setdefault(task_name, []).append(scores)
    _record_metrics(runtime, task_name, round_num, scores, gen_dur + eval_dur, state.dims)

    action = _decide_next(scores, round_num, state.history, state.dims)
    _append_session_event(
        "harness.decision",
        {"task": task_name, "round": round_num, "action": action},
    )
    state.step_results.append(
        {
            "round": round_num,
            "scores": scores,
            "action": action,
            "gen_duration_s": gen_dur,
            "eval_duration_s": eval_dur,
        }
    )
    return action


def _emit_harness_complete(task_name: str, state: _HarnessState) -> None:
    from openmax.lead_agent.tools._helpers import _append_session_event

    peak = _detect_quality_peak(state.history, state.dims)
    _append_session_event(
        "harness.complete",
        {
            "task": task_name,
            "total_rounds": len(state.history),
            "peak_round": peak,
            "final_scores": state.history[-1] if state.history else {},
        },
    )
    console.print(f"  {_H}  harness complete: {len(state.history)} rounds, peak at round {peak}")


async def run_harness_workflow(
    runtime: Any,
    task_name: str,
    task_prompt: str,
) -> list[dict[str, Any]]:
    """Execute planner → generator ↔ evaluator harness loop."""
    from openmax.task_file import read_evaluation

    state = _HarnessState(dims=_select_dimensions(runtime), spec="")
    state.spec = await _run_planner_phase(runtime, task_name, task_prompt)

    for round_num in range(1, MAX_HARNESS_ROUNDS + 1):
        action = await _run_harness_round(runtime, task_name, task_prompt, round_num, state)
        console.print(f"  {_H}  round {round_num} decision: {action}")
        if action == "accept":
            break
        if action == "pivot":
            state.spec = await _run_planner_phase(runtime, task_name, task_prompt)
        state.prev_eval_text = read_evaluation(runtime.cwd, task_name, round_num) or ""

    _emit_harness_complete(task_name, state)
    return state.step_results
