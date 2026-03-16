"""Environment health check for openMax."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    ok: bool
    version: str | None = None
    detail: str | None = None
    fix_hint: str | None = None


def _get_version(cmd: str, flag: str = "--version") -> str | None:
    try:
        result = subprocess.run(
            [cmd, flag],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (result.stdout + result.stderr).strip()
        # grab first non-empty line, trim to 20 chars
        first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
        return first[:40] or None
    except Exception:
        return None


def _check_python() -> CheckResult:
    v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 10)
    return CheckResult(
        name="Python",
        ok=ok,
        version=v,
        fix_hint=None if ok else "openMax requires Python 3.10+",
    )


def _check_terminal_backends() -> list[CheckResult]:
    """Check kaku and tmux — only flag an issue if neither is available."""
    has_kaku = shutil.which("kaku") is not None
    has_tmux = shutil.which("tmux") is not None
    results: list[CheckResult] = []

    if has_kaku:
        v = _get_version("kaku")
        results.append(CheckResult(name="Kaku CLI", ok=True, version=v))
    else:
        results.append(
            CheckResult(
                name="Kaku CLI",
                ok=has_tmux,  # not a problem if tmux is available
                detail="not installed" if has_tmux else None,
                fix_hint=None if has_tmux else "brew install --cask kaku",
            )
        )

    if has_tmux:
        v = _get_version("tmux", "-V")
        in_session = os.environ.get("TMUX") is not None
        detail = "in session" if in_session else "installed (not in session)"
        results.append(CheckResult(name="tmux", ok=True, version=v, detail=detail))
    else:
        results.append(
            CheckResult(
                name="tmux",
                ok=has_kaku,  # not a problem if kaku is available
                detail="not installed" if has_kaku else None,
                fix_hint=None if has_kaku else "brew install tmux  (or apt install tmux)",
            )
        )

    return results


def _check_cli(name: str, cmd: str, fix: str) -> CheckResult:
    found = shutil.which(cmd)
    if not found:
        return CheckResult(name=name, ok=False, fix_hint=fix)
    v = _get_version(cmd)
    return CheckResult(name=name, ok=True, version=v)


def _check_claude_auth() -> CheckResult:
    from openmax.auth import has_claude_auth

    ok, detail = has_claude_auth()
    if ok:
        return CheckResult(name="Claude auth", ok=True, detail=detail)
    return CheckResult(
        name="Claude auth",
        ok=False,
        fix_hint="Run `openmax setup` to configure authentication",
    )


def _check_openai_auth() -> CheckResult:
    if os.environ.get("OPENAI_API_KEY"):
        return CheckResult(name="Codex auth", ok=True, detail="OPENAI_API_KEY set")
    return CheckResult(
        name="Codex auth",
        ok=False,
        detail="optional — only needed if using codex agent",
        fix_hint="export OPENAI_API_KEY=<your-key>",
    )


def run_checks() -> list[CheckResult]:
    return [
        _check_python(),
        *_check_terminal_backends(),
        _check_cli("claude", "claude", "See https://docs.anthropic.com/en/docs/claude-code"),
        _check_cli("codex", "codex", "npm install -g @openai/codex  (optional)"),
        _check_cli(
            "opencode",
            "opencode",
            "See https://github.com/opencode-ai/opencode  (optional)",
        ),
        _check_claude_auth(),
        _check_openai_auth(),
    ]


def render_results(results: list[CheckResult]) -> tuple[list[str], int]:
    """Return (lines, issue_count)."""
    lines: list[str] = ["openMax environment check", "─" * 42]
    issues = 0
    for r in results:
        icon = "✅" if r.ok else "❌"
        ver = f"  {r.version}" if r.version else ""
        detail = f"  ({r.detail})" if r.detail else ""
        lines.append(f"  {icon}  {r.name:<18}{ver}{detail}")
        if not r.ok:
            issues += 1
            if r.fix_hint:
                lines.append(f"       Fix: {r.fix_hint}")
    lines.append("─" * 42)
    if issues == 0:
        lines.append("All checks passed ✅")
    else:
        noun = "issue" if issues == 1 else "issues"
        lines.append(f"{issues} {noun} found.")
    return lines, issues
