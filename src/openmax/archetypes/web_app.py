from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="web",
    display_name="Web Application",
    description="Full-stack or frontend web application with UI components",
    subtask_templates=[
        SubtaskTemplate(
            name="frontend",
            description="UI components, pages, and client-side logic",
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="backend",
            description="Server-side logic, data processing, business rules",
            dependencies=["frontend"],
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="api_routes",
            description="API route handlers and request/response schemas",
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="tests",
            description="Unit and integration tests for web functionality",
            dependencies=["frontend", "backend", "api_routes"],
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="deploy_config",
            description="Deployment configuration, Docker, CI/CD",
            dependencies=["tests"],
            estimated_minutes=5,
        ),
    ],
    planning_hints=[
        "Identify whether changes are frontend-only, backend-only, or full-stack",
        "Check for existing component libraries or design systems before creating new ones",
        "Consider SSR vs CSR implications for new pages",
        "Map API contract changes before touching frontend consumers",
        "Look for shared state management patterns already in use",
    ],
    anti_patterns=[
        "Adding new API endpoints without updating frontend consumers",
        "Mixing business logic into route handlers or components",
        "Skipping responsive/accessibility considerations",
    ],
)
