"""Loop session tape: persistent record of iterations for openmax loop.

Inspired by bub's tape-based context design — each iteration is recorded to
a JSONL file so subsequent iterations know exactly what was done and can avoid
repeating completed work.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openmax._paths import utc_now_iso


def _loops_dir() -> Path:
    from openmax._paths import default_sessions_dir

    return default_sessions_dir() / "loops"


@dataclass
class LoopIteration:
    iteration: int
    session_id: str | None
    started_at: str
    completed_at: str | None
    outcome_summary: str
    completion_pct: int | None
    tasks_done: list[str]
    tasks_failed: list[str]


@dataclass
class LoopSession:
    loop_id: str
    goal: str
    cwd: str
    created_at: str = field(default_factory=utc_now_iso)
    iterations: list[LoopIteration] = field(default_factory=list)


class LoopSessionStore:
    def __init__(self) -> None:
        _loops_dir().mkdir(parents=True, exist_ok=True)

    def _path(self, loop_id: str) -> Path:
        return _loops_dir() / f"{loop_id}.jsonl"

    def create(self, goal: str, cwd: str) -> LoopSession:
        session = LoopSession(loop_id=uuid.uuid4().hex[:12], goal=goal, cwd=cwd)
        fields = {k: v for k, v in asdict(session).items() if k != "iterations"}
        header = {"type": "header", **fields}
        self._path(session.loop_id).write_text(json.dumps(header) + "\n", encoding="utf-8")
        return session

    def append_iteration(self, loop_id: str, iteration: LoopIteration) -> None:
        entry = {"type": "iteration", **asdict(iteration)}
        with self._path(loop_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self, loop_id: str) -> LoopSession | None:
        path = self._path(loop_id)
        if not path.exists():
            return None
        header: dict = {}
        iterations: list[LoopIteration] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("type") == "header":
                header = data
            elif data.get("type") == "iteration":
                data.pop("type", None)
                iterations.append(LoopIteration(**data))
        if not header:
            return None
        return LoopSession(
            loop_id=header["loop_id"],
            goal=header["goal"],
            cwd=header["cwd"],
            created_at=header["created_at"],
            iterations=iterations,
        )


_LOOP_CONTEXT_MAX_ITERATIONS = 10


def build_loop_context(session: LoopSession, current_iteration: int) -> str:
    """Build a context block summarising prior iterations for the lead agent.

    Caps at the last 10 iterations to keep prompt size bounded.
    """
    if not session.iterations:
        return ""
    lines = [
        f"## Loop Context (Iteration {current_iteration} — Loop {session.loop_id})",
        f"Overall goal: {session.goal}",
        "",
        "Completed iterations — DO NOT repeat any of this work:",
    ]
    recent = session.iterations[-_LOOP_CONTEXT_MAX_ITERATIONS:]
    n_total = len(session.iterations)
    if n_total > _LOOP_CONTEXT_MAX_ITERATIONS:
        lines.append(f"  (showing last {_LOOP_CONTEXT_MAX_ITERATIONS} of {n_total} iterations)")
    for it in recent:
        ts = it.started_at[:16].replace("T", " ")
        pct = f"{it.completion_pct}%" if it.completion_pct is not None else "?"
        lines.append(f"  {it.iteration}. [{ts}] {it.outcome_summary}  ({pct})")
        if it.tasks_done:
            lines.append(f"     Done: {', '.join(it.tasks_done[:10])}")
        if it.tasks_failed:
            lines.append(f"     Failed: {', '.join(it.tasks_failed[:5])}")
    lines += ["", "Pick the next unaddressed improvement that builds on what was done above."]
    return "\n".join(lines)


def _format_iteration_history(iterations: list[LoopIteration]) -> list[str]:
    recent = iterations[-_LOOP_CONTEXT_MAX_ITERATIONS:]
    lines: list[str] = []
    if len(iterations) > _LOOP_CONTEXT_MAX_ITERATIONS:
        lines.append(
            f"  (showing last {_LOOP_CONTEXT_MAX_ITERATIONS} of {len(iterations)} iterations)"
        )
    for it in recent:
        ts = it.started_at[:16].replace("T", " ")
        pct = f"{it.completion_pct}%" if it.completion_pct is not None else "?"
        lines.append(f"  {it.iteration}. [{ts}] {it.outcome_summary}  ({pct})")
        if it.tasks_done:
            lines.append(f"     Done: {', '.join(it.tasks_done[:10])}")
        if it.tasks_failed:
            lines.append(f"     Failed: {', '.join(it.tasks_failed[:5])}")
    return lines


def build_interactive_context(
    iteration: int,
    prior_iterations: list[LoopIteration],
    user_feedback: str,
) -> str:
    """Build context for interactive mode: prior results + user feedback."""
    lines = [
        f"## Interactive Session (Iteration {iteration})",
        "",
        "Prior iterations — DO NOT repeat completed work:",
    ]
    lines.extend(_format_iteration_history(prior_iterations))
    lines += [
        "",
        "User feedback for this iteration:",
        f"> {user_feedback}",
        "",
        "Address the user's feedback. Build on prior work, do not redo completed tasks.",
    ]
    return "\n".join(lines)
