"""Claude Code agent adapter."""

from __future__ import annotations

import logging

from openmax.adapters.base import AgentAdapter, AgentCommand

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code CLI (interactive mode).

    Launches `claude` interactively so the user can see plan mode,
    tool usage, and token consumption. Auth is handled by
    `claude setup-token` (stored in Claude's own config).
    """

    @property
    def agent_type(self) -> str:
        return "claude-code"

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        return AgentCommand(
            launch_cmd=["claude", prompt],
            interactive=True,
            ready_delay_seconds=0.3,
            ready_patterns=["❯", ">", "claude"],
            trust_patterns=["Yes, I trust"],
        )


class ClaudeCodePrintAdapter(AgentAdapter):
    """Adapter for Claude Code in non-interactive (print) mode.

    Used for one-shot tasks where interactive control isn't needed.
    """

    @property
    def agent_type(self) -> str:
        return "claude-code-print"

    @property
    def interactive(self) -> bool:
        return False

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        return AgentCommand(launch_cmd=["claude", "-p", prompt], interactive=False)


class ClaudeCodeStreamAdapter(AgentAdapter):
    """Claude Code in streaming print mode with structured JSON output."""

    @property
    def agent_type(self) -> str:
        return "claude-code-stream"

    @property
    def interactive(self) -> bool:
        return False

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", prompt]
        return AgentCommand(launch_cmd=cmd, interactive=False, stream_json=True)
