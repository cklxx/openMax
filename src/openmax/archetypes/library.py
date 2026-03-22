from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="library",
    display_name="Library / Package",
    description="Reusable library or package published for external consumption",
    subtask_templates=[
        SubtaskTemplate(
            name="core_module",
            description="Core library logic and internal implementation",
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="public_api",
            description="Public API surface, exports, and type signatures",
            dependencies=["core_module"],
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="docs",
            description="Docstrings, README, and usage examples",
            dependencies=["public_api"],
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="tests",
            description="Unit tests covering public API and edge cases",
            dependencies=["core_module", "public_api"],
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="packaging",
            description="Build config, versioning, and release setup",
            dependencies=["tests"],
            estimated_minutes=3,
        ),
    ],
    planning_hints=[
        "Audit the public API surface before adding new exports",
        "Check semver implications: breaking change = major bump",
        "Ensure new features have corresponding type stubs if py.typed is present",
        "Review existing test patterns before writing new ones",
    ],
    anti_patterns=[
        "Exposing internal implementation details in the public API",
        "Adding required dependencies for optional features",
        "Breaking backward compatibility without a major version bump",
    ],
)
