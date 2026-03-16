"""Task-category taxonomy, classification, and prediction logic."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from openmax.memory._utils import _keywords, infer_code_scope

# ── Task-category taxonomy for query distribution ─────────────────
_TASK_CATEGORIES: dict[str, list[str]] = {
    "code": [
        "implement",
        "code",
        "write",
        "function",
        "class",
        "module",
        "feature",
        "endpoint",
        "handler",
        "route",
        "scaffold",
        "create",
    ],
    "testing": [
        "test",
        "tests",
        "pytest",
        "coverage",
        "assert",
        "spec",
        "unittest",
        "integration",
        "e2e",
    ],
    "debugging": [
        "fix",
        "bug",
        "error",
        "crash",
        "debug",
        "issue",
        "broken",
        "fail",
        "trace",
        "diagnose",
    ],
    "refactor": [
        "refactor",
        "rename",
        "extract",
        "move",
        "cleanup",
        "simplify",
        "reorganize",
        "deduplicate",
        "restructure",
    ],
    "architecture": [
        "architect",
        "design",
        "pattern",
        "structure",
        "schema",
        "migration",
        "database",
        "infra",
        "deploy",
        "ci",
        "cd",
        "pipeline",
    ],
    "docs": [
        "doc",
        "docs",
        "readme",
        "changelog",
        "comment",
        "docstring",
        "documentation",
        "guide",
        "tutorial",
    ],
}

# Reverse index: keyword → category
_KEYWORD_TO_CATEGORY: dict[str, str] = {}
for _cat, _words in _TASK_CATEGORIES.items():
    for _w in _words:
        _KEYWORD_TO_CATEGORY[_w] = _cat

# ── Prediction templates ──────────────────────────────────────────
# Maps (completed-category, outcome) to likely follow-up queries.
_PREDICTION_TEMPLATES: dict[str, list[str]] = {
    "code:success": [
        "write tests for the new {scope}",
        "review and refactor {scope}",
        "add documentation for {scope}",
    ],
    "code:partial": [
        "continue implementing {scope}",
        "debug the remaining issues in {scope}",
    ],
    "testing:success": [
        "improve coverage for {scope}",
        "refactor {scope} based on test feedback",
    ],
    "testing:partial": [
        "fix failing tests in {scope}",
        "debug test errors in {scope}",
    ],
    "debugging:success": [
        "add regression tests for the fix in {scope}",
        "refactor {scope} to prevent similar bugs",
    ],
    "debugging:partial": [
        "continue debugging {scope}",
        "investigate root cause in {scope}",
    ],
    "refactor:success": [
        "write tests for the refactored {scope}",
        "update docs after refactoring {scope}",
    ],
    "refactor:partial": [
        "continue refactoring {scope}",
        "fix regressions from refactor in {scope}",
    ],
    "architecture:success": [
        "implement the designed changes in {scope}",
        "write migration scripts for {scope}",
    ],
    "docs:success": [
        "implement changes described in {scope} docs",
    ],
}


def classify_task(task: str) -> str:
    """Classify a task description into one of the known categories."""
    tokens = _keywords(task)
    scores: dict[str, int] = defaultdict(int)
    for token in tokens:
        cat = _KEYWORD_TO_CATEGORY.get(token)
        if cat:
            scores[cat] += 1
    if not scores:
        return "code"  # default
    return max(scores, key=lambda c: scores[c])


def predict_next_queries(
    task: str,
    completion_pct: int,
    subtasks: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Generate predicted follow-up queries based on what just finished."""
    category = classify_task(task)
    outcome = "success" if completion_pct >= 80 else "partial"
    key = f"{category}:{outcome}"

    # Derive scope string from task + subtasks
    scope_tokens = infer_code_scope(task, subtasks=subtasks)
    scope = ", ".join(scope_tokens[:3]) if scope_tokens else "the codebase"

    templates = _PREDICTION_TEMPLATES.get(key, _PREDICTION_TEMPLATES.get(f"{category}:success", []))
    predictions = [tpl.format(scope=scope) for tpl in templates]

    # Also add generic continuation if partial
    if completion_pct < 80:
        predictions.insert(0, f"continue: {task}")

    return predictions[:4]
