"""Authentication helpers for openMax.

Wraps `claude setup-token` for a seamless setup experience and
provides token detection for health checks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


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


def has_claude_auth() -> tuple[bool, str]:
    """Check whether Claude authentication is available.

    Returns (ok, detail) tuple.
    """
    # 1. Setup token env var
    if os.environ.get("CLAUDE_CODE_SETUP_TOKEN"):
        return True, "setup token (env)"

    # 2. API key env var
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "ANTHROPIC_API_KEY env var"

    # 3. Probe via `claude auth status`
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
