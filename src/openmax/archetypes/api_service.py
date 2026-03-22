from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="api",
    display_name="API Service",
    description="REST or GraphQL API service with endpoints and middleware",
    indicators=[
        "routes/",
        "endpoints/",
        "openapi",
        "fastapi",
        "flask",
        "django",
        "swagger",
        "schema.graphql",
    ],
    subtask_templates=[
        SubtaskTemplate(
            name="endpoints",
            description="API endpoint handlers and request validation",
            files_pattern="src/**/routes/**/*",
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="middleware",
            description="Request/response middleware, CORS, rate limiting",
            files_pattern="src/**/middleware/**/*",
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="auth",
            description="Authentication and authorization logic",
            files_pattern="src/**/auth*",
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="models",
            description="Data models, schemas, and serialization",
            files_pattern="src/**/models/**/*",
            dependencies=["endpoints"],
            estimated_minutes=8,
        ),
        SubtaskTemplate(
            name="tests",
            description="API endpoint tests with test client",
            files_pattern="tests/test_api*.py",
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
