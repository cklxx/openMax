"""Tests for openmax.auth module."""

from __future__ import annotations

import json

from openmax.auth import (
    _check_claude_settings_api_key,
    _read_claude_settings_env,
    has_claude_auth,
)


def test_settings_api_key_detected(tmp_path, monkeypatch):
    """API key in ~/.claude/settings.json is recognized."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-test-123"}}))

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _check_claude_settings_api_key() == "settings (settings.json)"


def test_settings_local_takes_priority(tmp_path, monkeypatch):
    """settings.local.json is checked before settings.json."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-1"}}))
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-2"}})
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _check_claude_settings_api_key() == "settings (settings.local.json)"


def test_settings_no_api_key(tmp_path, monkeypatch):
    """No API key in settings returns None."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"permissions": {}}))

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _check_claude_settings_api_key() is None


def test_settings_no_claude_dir(tmp_path, monkeypatch):
    """Missing ~/.claude dir returns None."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _check_claude_settings_api_key() is None


def test_has_claude_auth_picks_up_settings(tmp_path, monkeypatch):
    """has_claude_auth returns True when API key is in settings."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-test"}}))

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    ok, detail = has_claude_auth()
    assert ok is True
    assert "settings" in detail


def test_has_claude_auth_env_var_before_settings(tmp_path, monkeypatch):
    """Env var ANTHROPIC_API_KEY takes priority over settings."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-settings"}})
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SETUP_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    ok, detail = has_claude_auth()
    assert ok is True
    assert detail == "ANTHROPIC_API_KEY env var"


# -- _read_claude_settings_env tests --


def test_read_settings_env_merges(tmp_path, monkeypatch):
    """Local settings override global, other keys merge."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_API_KEY": "sk-global",
                    "ANTHROPIC_BASE_URL": "https://global",
                }
            }
        )
    )
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-local"}})
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    env = _read_claude_settings_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-local"
    assert env["ANTHROPIC_BASE_URL"] == "https://global"


def test_read_settings_env_empty(tmp_path, monkeypatch):
    """No settings files returns empty dict."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _read_claude_settings_env() == {}


# -- _build_lead_env integration tests --


def test_build_lead_env_forwards_settings_api_key(tmp_path, monkeypatch):
    """API key from settings is forwarded when not in process env."""
    from openmax.lead_agent.core import _build_lead_env

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_API_KEY": "sk-from-settings",
                    "ANTHROPIC_BASE_URL": "https://custom",
                }
            }
        )
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    env = _build_lead_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-from-settings"
    assert env["ANTHROPIC_BASE_URL"] == "https://custom"
    assert env["CLAUDECODE"] == ""


def test_build_lead_env_prefers_process_env(tmp_path, monkeypatch):
    """Process env vars take precedence over settings."""
    from openmax.lead_agent.core import _build_lead_env

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-from-settings"}})
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")

    env = _build_lead_env()
    assert "ANTHROPIC_API_KEY" not in env  # not overridden; process env wins
