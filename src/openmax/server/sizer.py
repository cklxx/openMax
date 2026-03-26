"""LLM-based task size estimation using Haiku for cost efficiency."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openmax.server.queue import TaskSize

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You estimate the size of a software engineering task. "
    "Respond with JSON only: "
    '{"size": "small|medium|large", "confidence": 0.0-1.0, '
    '"reasoning": "one line", "priority": 0-100, "decompose": true|false}\n\n'
    "Guidelines:\n"
    "- small: single file, typo fix, config change, <10 min\n"
    "- medium: 2-5 files, needs tests, clear scope, 10-30 min\n"
    "- large: 5+ files, cross-module refactor, new feature, >30 min\n"
    "- priority: 0=highest. bug fixes ~10, features ~30, docs ~60, chores ~80\n"
    "- decompose: true if large and can be split into independent subtasks"
)


@dataclass
class SizeEstimate:
    size: TaskSize
    confidence: float
    reasoning: str
    suggested_priority: int
    should_decompose: bool


def estimate_task_size(task: str, cwd: str) -> SizeEstimate:
    """Estimate task size via Haiku. Falls back to MEDIUM on error."""
    try:
        import anthropic

        resp = anthropic.Anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Task: {task}\nProject dir: {cwd}"}],
        )
        raw = json.loads(resp.content[0].text.strip())
        return SizeEstimate(
            size=TaskSize(raw["size"]),
            confidence=float(raw.get("confidence", 0.5)),
            reasoning=raw.get("reasoning", ""),
            suggested_priority=int(raw.get("priority", 50)),
            should_decompose=bool(raw.get("decompose", False)),
        )
    except Exception as exc:
        logger.warning("Task sizing failed, defaulting to medium: %s", exc)
        return SizeEstimate(
            size=TaskSize.MEDIUM,
            confidence=0.0,
            reasoning="sizing failed — default",
            suggested_priority=50,
            should_decompose=False,
        )
