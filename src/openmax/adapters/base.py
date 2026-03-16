
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentCommand:
    """Describes how to launch an agent.

    For interactive agents: launch_cmd starts the CLI,
    then initial_input is sent via kaku send-text.

    For non-interactive agents: launch_cmd includes the prompt,
    initial_input is None.
    """

    launch_cmd: list[str]
    initial_input: str | None = None
    interactive: bool = True
    ready_delay_seconds: float = 3.0
    env: dict[str, str] = field(default_factory=dict, repr=False)
    ready_patterns: list[str] = field(default_factory=list, repr=False)
    """Strings to poll for in pane output before sending initial_input.
    Empty list = fall back to ready_delay_seconds sleep."""


class AgentAdapter(ABC):
    """Abstract base class for agent adapters."""

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Identifier for this agent type."""
        ...

    @property
    def interactive(self) -> bool:
        """Whether this agent runs interactively (default True)."""
        return True

    @abstractmethod
    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        """Return the command spec to start this agent with the given prompt."""
        ...
