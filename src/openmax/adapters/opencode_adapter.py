"""OpenCode CLI agent adapter."""

from openmax.adapters.base import AgentAdapter, AgentCommand


class OpenCodeAdapter(AgentAdapter):
    """Adapter for OpenCode CLI (interactive mode)."""

    @property
    def agent_type(self) -> str:
        return "opencode"

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        return AgentCommand(
            launch_cmd=["opencode"],
            initial_input=prompt,
            interactive=True,
            ready_patterns=["opencode>", "> ", "❯ "],
        )
