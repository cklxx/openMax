"""Claude Code agent adapter."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from openmax.adapters.base import AgentAdapter, AgentCommand

logger = logging.getLogger(__name__)


def _read_claude_oauth_token() -> str | None:
    """Read the OAuth token from ~/.claude/.credentials.json if available."""
    creds_path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(creds_path.read_text(encoding="utf-8"))
        token = data.get("claudeAiOauth", {}).get("accessToken")
        if token:
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code CLI (interactive mode).

    Launches `claude` interactively so the user can see plan mode,
    tool usage, and token consumption. The initial prompt is sent
    via kaku send-text.

    Automatically injects CLAUDE_CODE_OAUTH_TOKEN from
    ~/.claude/.credentials.json when available.
    """

    @property
    def agent_type(self) -> str:
        return "claude-code"

    def get_command(self, prompt: str, cwd: str | None = None) -> AgentCommand:
        launch = ["claude"]
        if cwd:
            launch.extend(["--add-dir", cwd])
        env: dict[str, str] = {}
        token = _read_claude_oauth_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return AgentCommand(
            launch_cmd=launch,
            initial_input=prompt,
            interactive=True,
            ready_patterns=["? for shortcuts", "Human:", "╭─"],
            env=env,
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
        env: dict[str, str] = {}
        token = _read_claude_oauth_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return AgentCommand(launch_cmd=cmd, interactive=False, env=env)
