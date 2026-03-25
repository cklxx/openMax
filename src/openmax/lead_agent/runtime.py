"""Mutable runtime state for a single lead-agent session."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openmax.session_runtime import SessionMeta, SessionStore

if TYPE_CHECKING:
    from openmax.mailbox import SessionMailbox


@dataclass
class LeadAgentRuntime:
    """Mutable runtime state for a single lead-agent session."""

    cwd: str
    plan: Any
    pane_mgr: Any
    agent_window_id: int | None = None
    session_store: SessionStore | None = None
    session_meta: SessionMeta | None = None
    allowed_agents: list[str] | None = None
    agent_registry: Any | None = None
    dashboard: Any | None = None
    pane_output_hashes: dict[int, list[str]] = field(default_factory=dict)
    plan_confirm: bool = True
    plan_submitted: bool = False
    current_phase: str = "research"
    integration_branch: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    mailbox: SessionMailbox | None = None
    mailbox_messaged_tasks: set[str] = field(default_factory=set)
    session_stats: Any | None = None
    quality_mode: bool = False
    quality_phases: dict[str, str] = field(default_factory=dict)  # task_name → phase
    ui_coordinator: Any | None = None


_lead_agent_runtime: ContextVar[LeadAgentRuntime | None] = ContextVar(
    "openmax_lead_agent_runtime",
    default=None,
)


def bind_lead_agent_runtime(
    runtime: LeadAgentRuntime,
) -> Token[LeadAgentRuntime | None]:
    return _lead_agent_runtime.set(runtime)


def reset_lead_agent_runtime(token: Token[LeadAgentRuntime | None]) -> None:
    _lead_agent_runtime.reset(token)


def get_lead_agent_runtime() -> LeadAgentRuntime:
    runtime = _lead_agent_runtime.get()
    if runtime is None:
        raise RuntimeError("Lead agent runtime is not initialized")
    return runtime
