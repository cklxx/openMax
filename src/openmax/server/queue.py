"""Filesystem-backed task queue for openMax serve mode."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class QueueStatus(str, Enum):
    QUEUED = "queued"
    SIZING = "sizing"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class TaskSize(str, Enum):
    UNKNOWN = "unknown"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


SLOT_COST = {TaskSize.SMALL: 1, TaskSize.MEDIUM: 2, TaskSize.LARGE: 3, TaskSize.UNKNOWN: 2}


@dataclass
class SubtaskInfo:
    name: str
    status: str = "pending"
    progress_pct: int = 0
    agent_type: str = ""


@dataclass
class ActivityEntry:
    timestamp: str
    source: str  # subtask name or "system"
    message: str
    type: str = "info"  # info | done | error | question


_MAX_ACTIVITY = 200


@dataclass
class QueuedTask:
    id: str
    task: str
    status: QueueStatus = QueueStatus.QUEUED
    priority: int = 50
    size: TaskSize = TaskSize.UNKNOWN
    size_confidence: float = 0.0
    size_override: bool = False
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    session_id: str | None = None
    subtasks: list[SubtaskInfo] = field(default_factory=list)
    activity: list[ActivityEntry] = field(default_factory=list)
    cwd: str = "."
    error: str | None = None

    def add_activity(self, source: str, message: str, entry_type: str = "info") -> ActivityEntry:
        from openmax._paths import utc_now_iso

        entry = ActivityEntry(utc_now_iso(), source, message, entry_type)
        self.activity.append(entry)
        if len(self.activity) > _MAX_ACTIVITY:
            self.activity = self.activity[-_MAX_ACTIVITY:]
        return entry

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueuedTask:
        subs = [SubtaskInfo(**s) for s in data.pop("subtasks", [])]
        acts = [ActivityEntry(**a) for a in data.pop("activity", [])]
        data["status"] = QueueStatus(data["status"])
        data["size"] = TaskSize(data["size"])
        return cls(**data, subtasks=subs, activity=acts)


class TaskQueue:
    """Filesystem-backed priority task queue. Thread-safe via asyncio.Lock."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or Path.home() / ".openmax" / "queue"
        self._tasks_dir = self._base / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, QueuedTask] = {}
        self._lock = asyncio.Lock()
        self._load_all()

    def _load_all(self) -> None:
        for p in self._tasks_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text("utf-8"))
                task = QueuedTask.from_dict(data)
                self._tasks[task.id] = task
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    def _persist(self, task: QueuedTask) -> None:
        path = self._tasks_dir / f"{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), "utf-8")

    def add(self, task_text: str, cwd: str, priority: int = 50) -> QueuedTask:
        from openmax._paths import utc_now_iso

        t = QueuedTask(
            id=uuid.uuid4().hex[:12],
            task=task_text,
            cwd=cwd,
            priority=priority,
            created_at=utc_now_iso(),
        )
        self._tasks[t.id] = t
        self._persist(t)
        return t

    def get(self, task_id: str) -> QueuedTask | None:
        return self._tasks.get(task_id)

    def update(self, task: QueuedTask) -> None:
        self._tasks[task.id] = task
        self._persist(task)

    def remove(self, task_id: str) -> bool:
        if task_id not in self._tasks:
            return False
        del self._tasks[task_id]
        (self._tasks_dir / f"{task_id}.json").unlink(missing_ok=True)
        return True

    def list_all(self) -> list[QueuedTask]:
        return sorted(self._tasks.values(), key=lambda t: (t.priority, t.created_at))

    def next_runnable(self) -> QueuedTask | None:
        """Return highest-priority queued task ready to run."""
        for t in self.list_all():
            if t.status == QueueStatus.QUEUED and t.size != TaskSize.UNKNOWN:
                return t
        return None

    def running_slot_cost(self) -> int:
        return sum(
            SLOT_COST.get(t.size, 2)
            for t in self._tasks.values()
            if t.status == QueueStatus.RUNNING
        )

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in self._tasks.values():
            key = t.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
