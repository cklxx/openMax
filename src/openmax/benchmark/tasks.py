"""Benchmark task definitions and loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class BenchmarkTask:
    """A single benchmark task with setup, prompt, and verification."""

    id: str
    name: str
    difficulty: str
    prompt: str
    setup_script: str
    verify_script: str
    success_pattern: str
    timeout_seconds: int = 300
    tags: list[str] = field(default_factory=list)


def load_task(path: Path) -> BenchmarkTask:
    """Load a single benchmark task from a YAML file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BenchmarkTask(
        id=data["id"],
        name=data["name"],
        difficulty=data.get("difficulty", "medium"),
        prompt=data["prompt"],
        setup_script=data.get("setup_script", ""),
        verify_script=data["verify_script"],
        success_pattern=data.get("success_pattern", "passed"),
        timeout_seconds=data.get("timeout_seconds", 300),
        tags=data.get("tags", []),
    )


def load_task_suite(suite_dir: Path | None = None) -> list[BenchmarkTask]:
    """Load all YAML tasks from a directory, sorted by difficulty."""
    if suite_dir is None:
        suite_dir = Path(__file__).parent / "task_suite"
    difficulty_order = {"small": 0, "medium": 1, "large": 2}
    tasks = [load_task(p) for p in sorted(suite_dir.glob("*.yaml"))]
    tasks.sort(key=lambda t: difficulty_order.get(t.difficulty, 99))
    return tasks
