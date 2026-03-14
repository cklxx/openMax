from __future__ import annotations

import pytest

from openmax.adapters.subprocess_adapter import EnvVarReference, SubprocessAdapter


def test_interactive_subprocess_adapter_quotes_cwd_for_shell_commands():
    adapter = SubprocessAdapter(
        "remote-codex",
        ["ssh", "devbox", "bash", "-lc", "cd {cwd_sh} && codex"],
        startup_delay=6.5,
    )

    command = adapter.get_command("Fix the flaky test", cwd="/tmp/my repo")

    assert command.launch_cmd == ["ssh", "devbox", "bash", "-lc", "cd '/tmp/my repo' && codex"]
    assert command.initial_input == "Fix the flaky test"
    assert command.ready_delay_seconds == 6.5


def test_noninteractive_subprocess_adapter_quotes_prompt_for_shell_commands():
    adapter = SubprocessAdapter(
        "remote-reviewer",
        ["ssh", "prod", "bash", "-lc", "tool --prompt {prompt_sh}"],
        is_interactive=False,
    )

    command = adapter.get_command("fix user's bug", cwd="/tmp/repo")

    expected = ["ssh", "prod", "bash", "-lc", "tool --prompt 'fix user'\"'\"'s bug'"]
    assert command.launch_cmd == expected
    assert command.initial_input is None
    assert command.interactive is False
    assert command.ready_delay_seconds == 0.0


def test_interactive_subprocess_adapter_resolves_setup_token_env_reference(monkeypatch):
    monkeypatch.setenv("OPENMAX_CLAUDE_SETUP_TOKEN", "setup-token-123")

    adapter = SubprocessAdapter(
        "claude-code",
        ["claude"],
        env={"CLAUDE_CODE_SETUP_TOKEN": EnvVarReference("OPENMAX_CLAUDE_SETUP_TOKEN")},
        startup_delay=1.0,
    )

    command = adapter.get_command("Review the auth flow")

    assert command.launch_cmd == ["env", "CLAUDE_CODE_SETUP_TOKEN=setup-token-123", "claude"]
    assert command.initial_input == "Review the auth flow"
    assert command.ready_delay_seconds == 1.0


def test_subprocess_adapter_requires_referenced_env_var(monkeypatch):
    monkeypatch.delenv("OPENMAX_CLAUDE_SETUP_TOKEN", raising=False)

    adapter = SubprocessAdapter(
        "claude-code",
        ["claude"],
        env={"CLAUDE_CODE_SETUP_TOKEN": EnvVarReference("OPENMAX_CLAUDE_SETUP_TOKEN")},
    )

    with pytest.raises(RuntimeError, match="OPENMAX_CLAUDE_SETUP_TOKEN"):
        adapter.get_command("Review the auth flow")
