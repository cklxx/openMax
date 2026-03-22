from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="api",
    display_name="API Service",
    description="REST or GraphQL API service with endpoints and middleware",
    subtask_templates=[
        SubtaskTemplate(
            name="endpoints",
            description="API endpoint handlers and request validation",
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="middleware",
            description="Request/response middleware, CORS, rate limiting",
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="auth",
            description="Authentication and authorization logic",
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="models",
            description="Data models, schemas, and serialization",
            dependencies=["endpoints"],
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="tests",
            description="API endpoint tests with test client",
            dependencies=["endpoints", "models"],
            estimated_minutes=8,
        ),
    ],
    planning_hints=[
        "Review OpenAPI/Swagger spec if available before modifying endpoints",
        "Check for existing authentication middleware before adding new auth logic",
        "Ensure backward compatibility for public API changes",
        "Map database migrations needed for model changes",
        "Consider rate limiting and caching implications",
    ],
    anti_patterns=[
        "Breaking API contracts without versioning",
        "Embedding database queries directly in route handlers",
        "Skipping input validation on public endpoints",
    ],
)
