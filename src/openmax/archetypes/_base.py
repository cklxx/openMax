from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubtaskTemplate:
    name: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    estimated_minutes: int = 5


@dataclass
class Archetype:
    name: str
    display_name: str
    description: str
    subtask_templates: list[SubtaskTemplate] = field(default_factory=list)
    planning_hints: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)


_CATEGORY_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "web": [
        ("frontend", 1.0),
        ("react", 1.0),
        ("vue", 1.0),
        ("html", 0.8),
        ("css", 0.8),
        ("web", 0.9),
        ("page", 0.6),
        ("ui", 0.7),
        ("component", 0.7),
        ("template", 0.6),
        ("browser", 0.8),
    ],
    "cli": [
        ("cli", 1.0),
        ("command", 0.7),
        ("terminal", 0.8),
        ("argparse", 1.0),
        ("click", 0.9),
        ("typer", 0.9),
        ("flag", 0.6),
        ("subcommand", 0.9),
    ],
    "api": [
        ("api", 1.0),
        ("endpoint", 1.0),
        ("rest", 0.9),
        ("graphql", 0.9),
        ("route", 0.8),
        ("middleware", 0.8),
        ("request", 0.6),
        ("response", 0.6),
        ("fastapi", 1.0),
        ("flask", 0.9),
        ("server", 0.7),
    ],
    "library": [
        ("library", 1.0),
        ("package", 0.8),
        ("module", 0.7),
        ("sdk", 0.9),
        ("publish", 0.7),
        ("pypi", 0.9),
        ("npm", 0.8),
        ("export", 0.6),
    ],
    "refactor": [
        ("refactor", 1.0),
        ("migrate", 1.0),
        ("restructure", 1.0),
        ("rewrite", 0.9),
        ("reorganize", 0.9),
        ("rename", 0.7),
        ("extract", 0.7),
        ("consolidate", 0.8),
        ("deprecate", 0.7),
    ],
}


def _score_category(task_lower: str, keywords: list[tuple[str, float]]) -> float:
    return sum(w for kw, w in keywords if kw in task_lower)


def classify_task(task: str) -> dict[str, float]:
    """Keyword-based scoring against categories."""
    task_lower = task.lower()
    return {cat: _score_category(task_lower, kws) for cat, kws in _CATEGORY_KEYWORDS.items()}


_MIN_CONFIDENCE = 0.5


def match_archetype(
    task: str,
    archetypes: list[Archetype],
) -> Archetype | None:
    """Find best archetype via keyword-weighted task classification."""
    if not archetypes:
        return None
    scores = classify_task(task)
    ranked = _rank_archetypes(archetypes, scores)
    if not ranked or ranked[0][1] < _MIN_CONFIDENCE:
        return None
    return ranked[0][0]


def _rank_archetypes(
    archetypes: list[Archetype],
    scores: dict[str, float],
) -> list[tuple[Archetype, float]]:
    pairs = [(a, scores.get(a.name, 0.0)) for a in archetypes]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs
