"""Git branch and worktree management for agent isolation."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import anyio

# Serializes all state-modifying git operations (checkout, merge, branch, worktree)
# to prevent race conditions when multiple agents finish concurrently.
_git_lock = anyio.Lock()


def _sanitize_branch_name(task_name: str) -> str:
    """Convert task name to a valid git branch name."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", task_name.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"openmax/{slug}" if slug else f"openmax/task-{int(time.time())}"


def _get_integration_branch(cwd: str) -> str | None:
    """Get the current git branch name, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _branch_exists(cwd: str, branch_name: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0


def _worktree_is_valid(worktree_dir: Path) -> bool:
    return (worktree_dir / ".git").exists()


def _add_worktree(
    cwd: str,
    worktree_dir: Path,
    branch_name: str,
    *,
    cleanup_branch: bool = True,
) -> tuple[str | None, str | None]:
    """Add a git worktree for an existing branch. Returns (path, error)."""
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), branch_name],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        if cleanup_branch:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=cwd,
                capture_output=True,
                timeout=10,
            )
        return None, f"Failed to create worktree: {result.stderr.strip()}"
    return str(worktree_dir), None


def _create_agent_branch(cwd: str, branch_name: str) -> tuple[str | None, str | None]:
    """Create or reuse a git branch and worktree for an agent.

    Returns (worktree_path, error_message). On success error_message is None.
    """
    worktree_base = Path(cwd) / ".openmax-worktrees"
    worktree_dir = worktree_base / branch_name.replace("/", "_")

    try:
        if _branch_exists(cwd, branch_name) and _worktree_is_valid(worktree_dir):
            return str(worktree_dir), None

        if _branch_exists(cwd, branch_name):
            subprocess.run(["git", "worktree", "prune"], cwd=cwd, capture_output=True, timeout=10)
            if _worktree_is_valid(worktree_dir):
                return str(worktree_dir), None
            return _add_worktree(cwd, worktree_dir, branch_name, cleanup_branch=False)

        result = subprocess.run(
            ["git", "branch", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None, f"Failed to create branch: {result.stderr.strip()}"
        return _add_worktree(cwd, worktree_dir, branch_name, cleanup_branch=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"Git error: {e}"


def _cleanup_agent_branch(cwd: str, branch_name: str) -> str | None:
    """Remove worktree and delete branch. Returns error message or None."""
    worktree_base = Path(cwd) / ".openmax-worktrees"
    worktree_dir = worktree_base / branch_name.replace("/", "_")

    try:
        if worktree_dir.exists():
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir), "--force"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Cleanup error: {e}"
    return None
