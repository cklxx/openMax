from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="refactor",
    display_name="Refactor / Migration",
    description="Code restructuring, migration, or large-scale renaming",
    indicators=[
        "refactor",
        "migrate",
        "restructure",
        "rewrite",
        "reorganize",
        "consolidate",
    ],
    subtask_templates=[
        SubtaskTemplate(
            name="analysis",
            description="Analyze current structure and map dependencies",
            files_pattern="src/**/*.py",
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="incremental_changes",
            description="Apply refactoring in small, testable increments",
            files_pattern="src/**/*.py",
            dependencies=["analysis"],
            estimated_minutes=15,
        ),
        SubtaskTemplate(
            name="test_updates",
            description="Update tests to match refactored code",
            files_pattern="tests/**/*",
            dependencies=["incremental_changes"],
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="verification",
            description="Run full test suite and verify no regressions",
            files_pattern="tests/**/*",
            dependencies=["test_updates"],
            estimated_minutes=5,
        ),
    ],
    planning_hints=[
        "Map all callers/dependents before moving or renaming",
        "Prefer incremental commits over one big-bang refactor",
        "Keep old and new paths working simultaneously during migration",
        "Run tests after each incremental step, not just at the end",
        "Document the migration path for other contributors",
    ],
    anti_patterns=[
        "Refactoring and adding new features in the same PR",
        "Renaming without updating all call sites in one atomic commit",
        "Skipping test runs between incremental changes",
    ],
)
