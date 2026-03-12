"""Generic subprocess agent adapter for arbitrary CLI agents."""

from openmax.adapters.base import AgentAdapter, AgentCommand


class SubprocessAdapter(AgentAdapter):
    """Adapter for any CLI-based agent.

    Supports both interactive and non-interactive modes.

    For interactive: `command_template` is the launch command (no prompt),
    prompt is sent via send-text.

    For non-interactive: `command_template` may contain `{prompt}` which
    gets replaced with the actual prompt.

    Examples:
        # Interactive agent
        SubprocessAdapter("my-agent", ["my-agent"], interactive=True)

        # Non-interactive agent
        SubprocessAdapter("my-agent", ["my-agent", "--prompt", "{prompt}"], interactive=False)
    """

    def __init__(
        self,
        name: str,
        command_template: list[str],
        is_interactive: bool = True,
    ) -> None:
        self._name = name
        self._command_template = command_template
        self._interactive = is_interactive

    @property
    def agent_type(self) -> str:
        return self._name

    @property
    def interactive(self) -> bool:
        return self._interactive

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        if self._interactive:
            return AgentCommand(
                launch_cmd=list(self._command_template),
                initial_input=prompt,
                interactive=True,
            )
        else:
            cmd = [part.replace("{prompt}", prompt) for part in self._command_template]
            return AgentCommand(launch_cmd=cmd, interactive=False)
