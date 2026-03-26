"""Workspace and session cleanup for openMax artifacts."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_OPENMAX_DIR = ".openmax"
_WORKTREES_DIR = ".openmax-worktrees"
_TASK_SUBDIRS = ("briefs", "reports", "shared", "checkpoints")
_SESSION_MAX_AGE_DAYS = 30
_SESSION_KEEP_MIN = 20


@dataclass
class CleanupReport:
    branches_removed: list[str] = field(default_factory=list)
    worktrees_removed: list[str] = field(default_factory=list)
    task_dirs_removed: list[str] = field(default_factory=list)
    message_logs_removed: list[str] = field(default_factory=list)
    sockets_removed: list[str] = field(default_factory=list)
    sessions_removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_removed(self) -> int:
        return (
            len(self.branches_removed)
            + len(self.worktrees_removed)
            + len(self.task_dirs_removed)
            + len(self.message_logs_removed)
            + len(self.sockets_removed)
            + len(self.sessions_removed)
        )


def scan_artifacts(cwd: str, *, include_sessions: bool = False) -> CleanupReport:
    """Scan for openMax artifacts without removing them (dry-run)."""
    report = CleanupReport()
    _scan_branches(cwd, report)
    _scan_worktrees(cwd, report)
    _scan_task_files(cwd, report)
    _scan_message_logs(cwd, report)
    _scan_sockets(report)
    if include_sessions:
        _scan_expired_sessions(report)
    return report


def clean_workspace(cwd: str, *, include_sessions: bool = False) -> CleanupReport:
    """Remove all openMax artifacts from the workspace."""
    report = CleanupReport()
    _remove_worktrees(cwd, report)
    _remove_branches(cwd, report)
    _remove_task_files(cwd, report)
    _remove_message_logs(cwd, report)
    _remove_sockets(report)
    if include_sessions:
        _expire_sessions(report)
    return report


def cleanup_branches_and_worktrees(cwd: str) -> list[str]:
    """Quick cleanup of branches and worktrees. Returns error messages."""
    errors: list[str] = []
    report = CleanupReport()
    _remove_worktrees(cwd, report)
    _remove_branches(cwd, report)
    errors.extend(report.errors)
    return errors


def expire_old_sessions(max_age_days: int = _SESSION_MAX_AGE_DAYS) -> int:
    """Remove sessions older than max_age_days. Returns count removed."""
    report = CleanupReport()
    _expire_sessions(report, max_age_days=max_age_days)
    return len(report.sessions_removed)


# -- Branch helpers ----------------------------------------------------------


def _list_openmax_branches(cwd: str) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "branch", "--list", "openmax/*"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
        return [b.strip().lstrip("* ") for b in r.stdout.splitlines() if b.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _scan_branches(cwd: str, report: CleanupReport) -> None:
    report.branches_removed = _list_openmax_branches(cwd)


def _remove_branches(cwd: str, report: CleanupReport) -> None:
    for branch in _list_openmax_branches(cwd):
        try:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=cwd,
                capture_output=True,
                timeout=10,
            )
            report.branches_removed.append(branch)
        except (OSError, subprocess.TimeoutExpired) as e:
            report.errors.append(f"branch {branch}: {e}")


# -- Worktree helpers --------------------------------------------------------


def _scan_worktrees(cwd: str, report: CleanupReport) -> None:
    wt_dir = Path(cwd) / _WORKTREES_DIR
    if wt_dir.exists():
        report.worktrees_removed = [d.name for d in wt_dir.iterdir() if d.is_dir()]


def _remove_worktrees(cwd: str, report: CleanupReport) -> None:
    wt_dir = Path(cwd) / _WORKTREES_DIR
    if not wt_dir.exists():
        return
    # Prune stale worktree refs first
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=cwd, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for d in list(wt_dir.iterdir()):
        if not d.is_dir():
            continue
        try:
            subprocess.run(
                ["git", "worktree", "remove", str(d), "--force"],
                cwd=cwd,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Force-remove if git worktree remove didn't clean it
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        report.worktrees_removed.append(d.name)
    # Remove the container dir if empty
    if wt_dir.exists() and not any(wt_dir.iterdir()):
        wt_dir.rmdir()


# -- Task file helpers -------------------------------------------------------


def _scan_task_files(cwd: str, report: CleanupReport) -> None:
    base = Path(cwd) / _OPENMAX_DIR
    for subdir in _TASK_SUBDIRS:
        d = base / subdir
        if d.exists() and any(d.iterdir()):
            report.task_dirs_removed.append(subdir)


def _remove_task_files(cwd: str, report: CleanupReport) -> None:
    base = Path(cwd) / _OPENMAX_DIR
    for subdir in _TASK_SUBDIRS:
        d = base / subdir
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            report.task_dirs_removed.append(subdir)


# -- Message log helpers -----------------------------------------------------


def _scan_message_logs(cwd: str, report: CleanupReport) -> None:
    base = Path(cwd) / _OPENMAX_DIR
    if base.exists():
        report.message_logs_removed = [f.name for f in base.glob("messages-*.jsonl")]


def _remove_message_logs(cwd: str, report: CleanupReport) -> None:
    base = Path(cwd) / _OPENMAX_DIR
    if not base.exists():
        return
    for f in base.glob("messages-*.jsonl"):
        f.unlink(missing_ok=True)
        report.message_logs_removed.append(f.name)


# -- Socket helpers ----------------------------------------------------------


def _scan_sockets(report: CleanupReport) -> None:
    tmp = Path("/tmp")
    report.sockets_removed = [f.name for f in tmp.glob("openmax-*.sock")]


def _remove_sockets(report: CleanupReport) -> None:
    for f in Path("/tmp").glob("openmax-*.sock"):
        try:
            f.unlink()
            report.sockets_removed.append(f.name)
        except OSError as e:
            report.errors.append(f"socket {f.name}: {e}")


# -- Session expiry ----------------------------------------------------------


def _sessions_dir() -> Path:
    return Path.home() / ".openmax" / "sessions"


def _scan_expired_sessions(
    report: CleanupReport,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> None:
    sessions_root = _sessions_dir()
    if not sessions_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    all_dirs = _collect_session_dirs(sessions_root)
    by_mtime = sorted(all_dirs, key=lambda d: d.stat().st_mtime, reverse=True)
    for d in by_mtime[_SESSION_KEEP_MIN:]:
        if d.stat().st_mtime < cutoff:
            report.sessions_removed.append(d.name)


def _expire_sessions(
    report: CleanupReport,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> None:
    sessions_root = _sessions_dir()
    if not sessions_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    all_dirs = _collect_session_dirs(sessions_root)
    by_mtime = sorted(all_dirs, key=lambda d: d.stat().st_mtime, reverse=True)
    for d in by_mtime[_SESSION_KEEP_MIN:]:
        if d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            report.sessions_removed.append(d.name)
    # Clean empty hash dirs
    for hash_dir in sessions_root.iterdir():
        if hash_dir.is_dir() and not any(hash_dir.iterdir()):
            hash_dir.rmdir()


def _collect_session_dirs(sessions_root: Path) -> list[Path]:
    """Collect all session directories (nested under hash dirs)."""
    dirs: list[Path] = []
    for hash_dir in sessions_root.iterdir():
        if not hash_dir.is_dir():
            continue
        for session_dir in hash_dir.iterdir():
            if session_dir.is_dir():
                dirs.append(session_dir)
    return dirs
