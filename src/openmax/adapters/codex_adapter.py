"""Codex CLI agent adapter."""

from openmax.adapters.base import AgentAdapter, AgentCommand


class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex CLI (interactive mode)."""

    @property
    def agent_type(self) -> str:
        return "codex"

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        return AgentCommand(
            launch_cmd=["codex"],
            initial_input=prompt,
            interactive=True,
        )


class CodexExecAdapter(AgentAdapter):
    """Adapter for Codex CLI in non-interactive (exec) mode."""

    @property
    def agent_type(self) -> str:
        return "codex-exec"

    @property
    def interactive(self) -> bool:
        return False

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        return AgentCommand(
            launch_cmd=["codex", "exec", prompt],
            interactive=False,
        )
