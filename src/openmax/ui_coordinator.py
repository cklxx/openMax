"""Serialized UI coordinator for multi-task mode."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from openmax.output import console


@dataclass
class UICoordinator:
    """Serializes all human-facing I/O across parallel lead-agent threads."""

    tasks: list[str]
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def print_banner(self, session_prefix: str) -> None:
        """Print a single unified banner listing all tasks."""
        from openmax.banner import print_banner as _print_banner

        _print_banner(task_count=len(self.tasks))
        for i, task in enumerate(self.tasks):
            console.print(f"    [dim]{i + 1}.[/dim] {task[:80]}")

    def request_input(self, task_label: str, prompt_fn: Callable[[], str]) -> str:
        """Acquire lock, show task label, run prompt_fn, release. Thread-safe."""
        with self._lock:
            console.print(f"\n  [bold cyan][{task_label}][/bold cyan]")
            return prompt_fn()
