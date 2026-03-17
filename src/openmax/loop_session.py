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


def build_loop_context(session: LoopSession, current_iteration: int) -> str:
    """Build a context block summarising all prior iterations for the lead agent."""
    if not session.iterations:
        return ""
    lines = [
        f"## Loop Context (Iteration {current_iteration} — Loop {session.loop_id})",
        f"Overall goal: {session.goal}",
        "",
        "Completed iterations — DO NOT repeat any of this work:",
    ]
    for it in session.iterations:
        ts = it.started_at[:16].replace("T", " ")
        pct = f"{it.completion_pct}%" if it.completion_pct is not None else "?"
        lines.append(f"  {it.iteration}. [{ts}] {it.outcome_summary}  ({pct})")
        if it.tasks_done:
            lines.append(f"     Done: {', '.join(it.tasks_done[:10])}")
        if it.tasks_failed:
            lines.append(f"     Failed: {', '.join(it.tasks_failed[:5])}")
    lines += ["", "Pick the next unaddressed improvement that builds on what was done above."]
    return "\n".join(lines)
