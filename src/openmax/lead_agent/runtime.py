"""Mutable runtime state for a single lead-agent session."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from openmax.session_runtime import SessionMeta, SessionStore


@dataclass
class LeadAgentRuntime:
    """Mutable runtime state for a single lead-agent session."""

    cwd: str
    plan: Any
    pane_mgr: Any
    agent_window_id: int | None = None
    session_store: SessionStore | None = None
    session_meta: SessionMeta | None = None
    memory_store: Any | None = None
    allowed_agents: list[str] | None = None
    agent_registry: Any | None = None
    dashboard: Any | None = None
    pane_output_hashes: dict[int, list[str]] = field(default_factory=dict)
    plan_submitted: bool = False
    current_phase: str = "research"
    integration_branch: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    sub_agent_model: str | None = None


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
