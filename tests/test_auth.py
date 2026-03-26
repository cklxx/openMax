"""Tests for openmax.auth module."""

from __future__ import annotations

import json

from openmax.auth import _check_claude_settings_api_key, has_claude_auth


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
    # Prevent shutil.which from finding claude binary
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
