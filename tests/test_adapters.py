from openmax.adapters.claude_code import ClaudeCodeAdapter
from openmax.adapters.codex_adapter import CodexAdapter
from openmax.adapters.subprocess_adapter import SubprocessAdapter


def test_claude_code_adapter_uses_positional_prompt_and_trust():
    adapter = ClaudeCodeAdapter()
    cmd = adapter.get_command("do something", cwd="/tmp")
    assert cmd.launch_cmd == ["claude", "do something"]
    assert cmd.initial_input is None
    assert len(cmd.trust_patterns) > 0


def test_codex_adapter_has_ready_patterns():
    adapter = CodexAdapter()
    cmd = adapter.get_command("do something")
    assert len(cmd.ready_patterns) > 0


def test_subprocess_adapter_passes_ready_patterns():
    adapter = SubprocessAdapter("myagent", ["my-cli"], ready_patterns=["ready>"])
    cmd = adapter.get_command("task")
    assert "ready>" in cmd.ready_patterns


def test_subprocess_adapter_default_ready_patterns():
    adapter = SubprocessAdapter("myagent", ["my-cli"])
    cmd = adapter.get_command("task")
    assert isinstance(cmd.ready_patterns, list)
