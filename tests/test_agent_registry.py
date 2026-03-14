from __future__ import annotations

from pathlib import Path

import pytest

from openmax.agent_registry import AgentConfigError, load_agent_registry


def test_load_agent_registry_adds_workspace_agents(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        """
[agents.remote-codex]
command = ["ssh", "devbox", "bash", "-lc", "cd {cwd_sh} && codex"]
interactive = true
startup_delay = 8
""".strip(),
        encoding="utf-8",
    )

    registry = load_agent_registry(str(tmp_path))

    assert "remote-codex" in registry.names()
    cmd = registry.get("remote-codex").get_command("Fix auth flow", cwd="/tmp/my repo")
    assert cmd.launch_cmd[-1] == "cd '/tmp/my repo' && codex"
    assert cmd.initial_input == "Fix auth flow"
    assert cmd.ready_delay_seconds == 8


def test_load_agent_registry_env_file_overrides_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    workspace_dir = tmp_path / ".openmax"
    workspace_dir.mkdir()
    (workspace_dir / "agents.toml").write_text(
        """
[agents.codex]
command = ["workspace-codex"]
interactive = true
""".strip(),
        encoding="utf-8",
    )

    env_file = tmp_path / "env-agents.toml"
    env_file.write_text(
        """
[agents.codex]
command = ["ssh", "prod", "codex"]
interactive = true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENMAX_AGENTS_FILE", str(env_file))

    registry = load_agent_registry(str(tmp_path))

    cmd = registry.get("codex").get_command("Review", cwd=str(tmp_path))
    assert cmd.launch_cmd == ["ssh", "prod", "codex"]


def test_load_agent_registry_resolves_claude_setup_token_env_reference(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENMAX_CLAUDE_SETUP_TOKEN", "setup-token-123")

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        """
[agents.claude-code]
command = ["claude"]
interactive = true

[agents.claude-code.env]
CLAUDE_CODE_SETUP_TOKEN = { env = "OPENMAX_CLAUDE_SETUP_TOKEN" }
""".strip(),
        encoding="utf-8",
    )

    registry = load_agent_registry(str(tmp_path))

    cmd = registry.get("claude-code").get_command("Review auth flow", cwd=str(tmp_path))
    assert cmd.launch_cmd == ["claude"]
    assert cmd.env == {"CLAUDE_CODE_SETUP_TOKEN": "setup-token-123"}
    assert cmd.initial_input == "Review auth flow"


def test_load_agent_registry_rejects_hardcoded_env_values(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        """
[agents.claude-code]
command = ["claude"]
interactive = true

[agents.claude-code.env]
CLAUDE_CODE_SETUP_TOKEN = "plain-text-secret"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(AgentConfigError, match="env.CLAUDE_CODE_SETUP_TOKEN"):
        load_agent_registry(str(tmp_path))


def test_load_agent_registry_rejects_noninteractive_without_prompt(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    config_path = config_dir / "agents.toml"
    config_path.write_text(
        """
[agents.remote-runner]
command = ["ssh", "prod", "codex", "exec"]
interactive = false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(AgentConfigError, match="non-interactive commands must include"):
        load_agent_registry(str(tmp_path))


def test_load_agent_registry_rejects_invalid_toml(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text("[agents.remote\n", encoding="utf-8")

    with pytest.raises(AgentConfigError, match="Invalid TOML"):
        load_agent_registry(str(tmp_path))


def test_load_agent_registry_requires_explicit_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENMAX_AGENTS_FILE", str(Path(tmp_path) / "missing.toml"))

    with pytest.raises(AgentConfigError, match="not found"):
        load_agent_registry(str(tmp_path))


def test_load_agent_registry_supports_agent_env_literals_and_env_refs(monkeypatch, tmp_path):
    secret = "sk-kimi-rJBeBAhtWvMxhHtUFWZ5eva8QvUsAt0ZoIVWAHM8Th197GNKKiNgGsAneYmkDbZy"
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KIMI_API_KEY", secret)

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        """
[agents.kimi-codex]
command = ["codex"]
interactive = true

[agents.kimi-codex.env]
OPENAI_BASE_URL = "https://api.moonshot.cn/v1"
OPENAI_API_KEY = { from_env = "KIMI_API_KEY" }
MOONSHOT_API_KEY = "sk-kimi-inline-token"
""".strip(),
        encoding="utf-8",
    )

    registry = load_agent_registry(str(tmp_path))

    cmd = registry.get("kimi-codex").get_command("Review auth flow", cwd=str(tmp_path))
    assert cmd.launch_cmd == ["codex"]
    assert cmd.env == {
        "OPENAI_BASE_URL": "https://api.moonshot.cn/v1",
        "OPENAI_API_KEY": secret,
        "MOONSHOT_API_KEY": "sk-kimi-inline-token",
    }
    assert secret not in " ".join(cmd.launch_cmd)


def test_load_agent_registry_rejects_missing_agent_env_ref(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_AGENTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MISSING_KIMI_API_KEY", raising=False)

    config_dir = tmp_path / ".openmax"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        """
[agents.kimi-codex]
command = ["codex"]
interactive = true

[agents.kimi-codex.env]
OPENAI_API_KEY = { from_env = "MISSING_KIMI_API_KEY" }
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(AgentConfigError, match="MISSING_KIMI_API_KEY"):
        load_agent_registry(str(tmp_path))
