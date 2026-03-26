"""Authentication module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_claude_setup_token() -> bool:
    """Run `claude setup-token` interactively, inheriting the TTY.

    Returns True if the command exits successfully.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False
    result = subprocess.run(
        [claude_bin, "setup-token"],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return result.returncode == 0


def _read_claude_settings_env() -> dict[str, str]:
    """Read the merged env dict from Claude Code settings files.

    Checks settings.local.json first (higher priority), then settings.json.
    Returns a merged dict with local overriding global.
    """
    merged: dict[str, str] = {}
    for name in ("settings.json", "settings.local.json"):
        path = Path.home() / ".claude" / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
            env = data.get("env") or {}
            merged.update(env)
        except Exception:
            continue
    return merged


_AUTH_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _check_claude_settings_api_key() -> str | None:
    """Check Claude Code settings files for API key / auth token in env."""
    for name in ("settings.local.json", "settings.json"):
        path = Path.home() / ".claude" / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
            env = data.get("env") or {}
            if any(env.get(k) for k in _AUTH_KEY_VARS):
                return f"settings ({name})"
        except Exception:
            continue
    return None


def has_claude_auth() -> tuple[bool, str]:
    """Check whether Claude authentication is available.

    Returns (ok, detail) tuple.
    """
    # 1. Setup token env var
    if os.environ.get("CLAUDE_CODE_SETUP_TOKEN"):
        return True, "setup token (env)"

    # 2. API key / auth token env var
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            return True, f"{var} env var"

    # 3. API key in Claude Code settings (users who set apiKey + baseUrl)
    settings_detail = _check_claude_settings_api_key()
    if settings_detail:
        return True, settings_detail

    # 4. Probe via `claude auth status`
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            result = subprocess.run(
                [claude_bin, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and "true" in result.stdout.lower():
                return True, "claude auth"
        except Exception:
            pass

    return False, "not configured"
