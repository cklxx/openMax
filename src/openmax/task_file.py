"""File-based context exchange between lead agent and sub-agents."""

from __future__ import annotations

from pathlib import Path

_OPENMAX_DIR = ".openmax"
_BRIEFS_DIR = "briefs"
_REPORTS_DIR = "reports"


def _task_dir(cwd: str, subdir: str) -> Path:
    return Path(cwd) / _OPENMAX_DIR / subdir


def _ensure_gitignore(cwd: str) -> None:
    """Create .openmax/.gitignore with '*' if it doesn't exist."""
    gi = Path(cwd) / _OPENMAX_DIR / ".gitignore"
    if not gi.exists():
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text("*\n", encoding="utf-8")


def write_brief(cwd: str, task_name: str, content: str) -> Path:
    """Write a task brief file. Returns the file path."""
    _ensure_gitignore(cwd)
    path = _task_dir(cwd, _BRIEFS_DIR) / f"{task_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def read_report(cwd: str, task_name: str) -> str | None:
    """Read a task completion report. Returns None if not found."""
    path = _task_dir(cwd, _REPORTS_DIR) / f"{task_name}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def brief_path(cwd: str, task_name: str) -> Path:
    return _task_dir(cwd, _BRIEFS_DIR) / f"{task_name}.md"


def report_path(cwd: str, task_name: str) -> Path:
    return _task_dir(cwd, _REPORTS_DIR) / f"{task_name}.md"


def cleanup_task_files(cwd: str, task_name: str) -> None:
    """Remove brief and report files for a task."""
    for subdir in (_BRIEFS_DIR, _REPORTS_DIR):
        path = _task_dir(cwd, subdir) / f"{task_name}.md"
        path.unlink(missing_ok=True)
