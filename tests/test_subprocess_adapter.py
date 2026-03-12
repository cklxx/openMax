from __future__ import annotations

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

    expected = ["ssh", "prod", "bash", "-lc", "tool --prompt 'fix user'\"'\"'s bug'"]
    assert command.launch_cmd == expected
    assert command.initial_input is None
    assert command.interactive is False
    assert command.ready_delay_seconds == 0.0
