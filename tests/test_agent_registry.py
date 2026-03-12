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
