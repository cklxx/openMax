from __future__ import annotations

import pytest

from openmax.adapters.subprocess_adapter import SubprocessAdapter


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

    expected = ["ssh", "prod", "bash", "-lc", "tool --prompt 'fix user'"'"'s bug'"]
    assert command.launch_cmd == expected
    assert command.initial_input is None
    assert command.interactive is False
    assert command.ready_delay_seconds == 0.0


def test_interactive_subprocess_adapter_carries_setup_token_env_without_leaking_into_command():
    adapter = SubprocessAdapter(
        "claude-code",
        ["claude"],
        env={"CLAUDE_CODE_SETUP_TOKEN": "setup-token-123"},
        startup_delay=1.0,
    )

    command = adapter.get_command("Review the auth flow")

    assert command.launch_cmd == ["claude"]
    assert command.env == {"CLAUDE_CODE_SETUP_TOKEN": "setup-token-123"}
    assert "setup-token-123" not in " ".join(command.launch_cmd)
    assert command.initial_input == "Review the auth flow"
    assert command.ready_delay_seconds == 1.0


def test_subprocess_adapter_carries_env_without_leaking_secret_into_command_or_repr():
    secret = "sk-kimi-rJBeBAhtWvMxhHtUFWZ5eva8QvUsAt0ZoIVWAHM8Th197GNKKiNgGsAneYmkDbZy"
    adapter = SubprocessAdapter(
        "kimi-codex",
        ["codex"],
        startup_delay=5,
        env={
            "OPENAI_API_KEY": secret,
            "OPENAI_BASE_URL": "https://api.moonshot.cn/v1",
        },
    )

    command = adapter.get_command("Fix the flaky test", cwd="/tmp/repo")

    assert command.launch_cmd == ["codex"]
    assert command.initial_input == "Fix the flaky test"
    assert command.ready_delay_seconds == 5
    assert command.env == {
        "OPENAI_API_KEY": secret,
        "OPENAI_BASE_URL": "https://api.moonshot.cn/v1",
    }
    assert secret not in " ".join(command.launch_cmd)
    assert secret not in repr(command)
