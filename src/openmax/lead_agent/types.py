"""Data types and error classification for the lead agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PERMANENT_ERROR = "permanent_error"


@dataclass
class SubTask:
    name: str
    agent_type: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    pane_id: int | None = None
    retry_count: int = 0
    max_retries: int = 2
    completion_notes: str | None = None
    branch_name: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    token_budget: int | None = None
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    usage_source: str = "estimated"
    role: str = "writer"
    employee: str | None = None
    estimated_cost_usd: float | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    goal: str
    subtasks: list[SubTask] = field(default_factory=list)


@dataclass
class PlannedSubtask:
    """A subtask in a structured plan, before dispatch."""

    name: str
    description: str
    files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    estimated_minutes: int | None = None
    agent_type: str | None = None


@dataclass
class PlanSubmission:
    """Structured output of the submit_plan tool."""

    subtasks: list[PlannedSubtask] = field(default_factory=list)
    rationale: str = ""
    parallel_groups: list[list[str]] = field(default_factory=list)
    total_budget: int | None = None


@dataclass
class LeadAgentStartupError(RuntimeError):
    category: str
    stage: str
    detail: str
    remediation: str

    def __post_init__(self) -> None:
        super().__init__(self.detail)

    @property
    def heading(self) -> str:
        if self.category == "authentication":
            return "Lead agent authentication failed"
        if self.category == "bootstrap":
            return "Lead agent bootstrap failed"
        return "Lead agent startup failed"

    def console_message(self) -> str:
        return (
            f"[bold red]{self.heading}[/bold red]\n"
            f"Stage: {self.stage}\n"
            f"Details: {self.detail}\n"
            f"Remediation: {self.remediation}"
        )

    def event_payload(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "stage": self.stage,
            "detail": self.detail,
            "remediation": self.remediation,
        }


def _classify_startup_failure(exc: Exception, stage: str) -> LeadAgentStartupError | None:
    detail = " ".join(str(exc).split()).strip() or exc.__class__.__name__
    normalized = detail.lower()

    auth_markers = (
        "auth",
        "login",
        "logged out",
        "unauthorized",
        "forbidden",
        "credential",
        "api key",
        "access token",
        "token expired",
        "permission denied",
        "401",
        "403",
    )
    bootstrap_markers = (
        "bootstrap",
        "startup",
        "start",
        "initialize",
        "initialise",
        "handshake",
        "failed to launch",
        "failed to start",
        "timed out",
        "timeout",
        "connection refused",
        "broken pipe",
        "transport",
    )

    if any(marker in normalized for marker in auth_markers):
        return LeadAgentStartupError(
            category="authentication",
            stage=stage,
            detail=detail,
            remediation=(
                "Run `openmax setup` to configure a long-lived API token, "
                "or set ANTHROPIC_API_KEY in ~/.claude/settings.json env."
            ),
        )

    if any(marker in normalized for marker in bootstrap_markers):
        return LeadAgentStartupError(
            category="bootstrap",
            stage=stage,
            detail=detail,
            remediation=(
                "Verify the Claude CLI can start cleanly in this environment, then retry. "
                "Check local shell setup, network access, and any required agent tooling."
            ),
        )

    if stage != "response_stream":
        return LeadAgentStartupError(
            category="startup",
            stage=stage,
            detail=detail,
            remediation=(
                "Retry after confirming the Claude CLI starts successfully in this shell. "
                "If the problem persists, inspect local environment and dependency setup."
            ),
        )

    return None
