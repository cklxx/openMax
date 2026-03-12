"""Claude Code agent adapter."""

from openmax.adapters.base import AgentAdapter, AgentCommand


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code CLI (interactive mode).

    Launches `claude` interactively so the user can see plan mode,
    tool usage, and token consumption. The initial prompt is sent
    via kaku send-text.
    """

    @property
    def agent_type(self) -> str:
        return "claude-code"

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        launch = ["claude"]
        if cwd:
            launch.extend(["--add-dir", cwd])
        return AgentCommand(
            launch_cmd=launch,
            initial_input=prompt,
            interactive=True,
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
        cmd = ["claude", "-p", prompt]
        if cwd:
            cmd.extend(["--add-dir", cwd])
        return AgentCommand(launch_cmd=cmd, interactive=False)
