"""Intelligent merge strategy: rebase for small diffs, merge for large ones."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SMALL_DIFF_MAX_FILES = 3
SMALL_DIFF_MAX_LINES = 100


def _git_run(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _diff_stats(branch: str, base: str, cwd: str) -> tuple[int, int]:
    """Return (changed_file_count, total_changed_lines) between base and branch."""
    result = _git_run(["git", "diff", "--stat", f"{base}...{branch}"], cwd)
    if result.returncode != 0:
        return 0, 0
    lines = result.stdout.strip().splitlines()
    if not lines:
        return 0, 0
    file_count = max(0, len(lines) - 1)
    total_lines = 0
    for line in lines[:-1]:
        parts = line.rsplit("|", 1)
        if len(parts) == 2:
            num = "".join(c for c in parts[1] if c.isdigit())
            total_lines += int(num) if num else 0
    return file_count, total_lines


def choose_merge_strategy(branch: str, base: str, cwd: str) -> str:
    """Choose rebase for small diffs, merge for large ones."""
    files, lines = _diff_stats(branch, base, cwd)
    if files <= SMALL_DIFF_MAX_FILES and lines <= SMALL_DIFF_MAX_LINES:
        return "rebase"
    return "merge"


def _conflicted_files(cwd: str) -> list[str]:
    """List files with unresolved merge conflicts."""
    result = _git_run(["git", "diff", "--name-only", "--diff-filter=U"], cwd)
    return [f for f in result.stdout.strip().splitlines() if f.strip()]


def _has_overlapping_markers(file_path: Path) -> bool:
    """Check if a file has conflict markers that overlap (same hunk)."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    in_conflict = False
    for line in content.splitlines():
        if line.startswith("<<<<<<<"):
            if in_conflict:
                return True
            in_conflict = True
        elif line.startswith(">>>>>>>"):
            in_conflict = False
    return in_conflict


def _try_auto_resolve_file(file_path: Path) -> bool:
    """Try to auto-resolve a trivially conflicted file.

    Only resolves files where conflict markers are well-formed
    and non-overlapping. Keeps the combined content (both sides).
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _has_overlapping_markers(file_path):
        return False
    resolved_lines: list[str] = []
    in_ours = False
    in_theirs = False
    for line in content.splitlines():
        if line.startswith("<<<<<<<"):
            in_ours = True
        elif line.startswith("=======") and in_ours:
            in_ours = False
            in_theirs = True
        elif line.startswith(">>>>>>>") and in_theirs:
            in_theirs = False
        else:
            resolved_lines.append(line)
    if in_ours or in_theirs:
        return False
    try:
        file_path.write_text("\n".join(resolved_lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def try_auto_resolve_conflicts(cwd: str) -> tuple[list[str], list[str]]:
    """Attempt to auto-resolve trivial conflicts after a failed merge.

    Returns (resolved_files, unresolved_files).
    """
    conflict_files = _conflicted_files(cwd)
    if not conflict_files:
        return [], []
    resolved: list[str] = []
    unresolved: list[str] = []
    for fname in conflict_files:
        fpath = Path(cwd) / fname
        if _try_auto_resolve_file(fpath):
            _git_run(["git", "add", fname], cwd)
            resolved.append(fname)
        else:
            unresolved.append(fname)
    return resolved, unresolved


def do_rebase(cwd: str, branch: str, base: str) -> tuple[bool, str]:
    """Rebase branch onto base. Returns (success, error_or_empty)."""
    result = _git_run(["git", "rebase", base, branch], cwd, timeout=60)
    if result.returncode == 0:
        return True, ""
    _git_run(["git", "rebase", "--abort"], cwd)
    return False, result.stderr.strip()
