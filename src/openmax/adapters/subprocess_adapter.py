"""Generic subprocess agent adapter for arbitrary CLI agents."""

import shlex
from collections.abc import Mapping

from openmax.adapters.base import AgentAdapter, AgentCommand


class SubprocessAdapter(AgentAdapter):
    """Adapter for any CLI-based agent.

    Supports both interactive and non-interactive modes.

    For interactive: `command_template` is the launch command (no prompt),
    prompt is sent via send-text.

    For non-interactive: `command_template` may contain `{prompt}` which
    gets replaced with the actual prompt.
    """

    def __init__(
        self,
        name: str,
        command_template: list[str],
        is_interactive: bool = True,
        startup_delay: float = 3.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._name = name
        self._command_template = command_template
        self._interactive = is_interactive
        self._startup_delay = startup_delay
        self._env = dict(env or {})

    @property
    def agent_type(self) -> str:
        return self._name

    @property
    def interactive(self) -> bool:
        return self._interactive

    def _render_template(self, value: str, prompt: str, cwd: str | None) -> str:
        replacements = {
            "{prompt}": prompt,
            "{prompt_sh}": shlex.quote(prompt),
            "{cwd}": cwd or "",
            "{cwd_sh}": shlex.quote(cwd or ""),
        }
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        command = [self._render_template(part, prompt, cwd) for part in self._command_template]
        if self._interactive:
            return AgentCommand(
                launch_cmd=command,
                initial_input=prompt,
                interactive=True,
                ready_delay_seconds=self._startup_delay,
                env=dict(self._env),
                ready_patterns=["$ ", "❯ ", "> "],
            )
        return AgentCommand(
            launch_cmd=command,
            interactive=False,
            ready_delay_seconds=0.0,
            env=dict(self._env),
        )
