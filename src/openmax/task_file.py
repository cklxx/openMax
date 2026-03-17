"""File-based context exchange between lead agent and sub-agents."""

from __future__ import annotations

from pathlib import Path

_OPENMAX_DIR = ".openmax"
_BRIEFS_DIR = "briefs"
_REPORTS_DIR = "reports"


def _task_dir(cwd: str, subdir: str) -> Path:
    return Path(cwd) / _OPENMAX_DIR / subdir


def _ensure_gitignore(cwd: str) -> None:
    """Ensure .openmax/ contents are git-ignored.

    Uses two layers:
    1. Nested .openmax/.gitignore with '*' (always works, no merge conflicts)
    2. Root .gitignore entry '.openmax/' (if root .gitignore exists and is tracked)
    """
    # Nested gitignore — always safe, no merge issues
    nested = Path(cwd) / _OPENMAX_DIR / ".gitignore"
    if not nested.exists():
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("*\n", encoding="utf-8")
    # Root gitignore — only append if file already exists (don't create it)
    root_gi = Path(cwd) / ".gitignore"
    entry = ".openmax/"
    if root_gi.exists():
        content = root_gi.read_text(encoding="utf-8")
        if entry not in content.splitlines():
            root_gi.write_text(content.rstrip("\n") + f"\n{entry}\n", encoding="utf-8")


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


_REPORT_INSTRUCTION = """\
When you complete your task, write a completion report to \
`.openmax/reports/{task_name}.md`:

```markdown
## Status
done | error | partial

## Summary
<What was accomplished in 1-2 sentences>

## Changes
- <file>: <what changed>

## Test Results
<pass/fail details>
```

This report is read by the orchestrator — always write it before finishing.\
"""


def inject_claude_md(cwd: str, task_name: str) -> None:
    """Append file-protocol instructions to CLAUDE.md in the agent's cwd.

    Claude Code auto-loads CLAUDE.md, making this more reliable than
    prompt injection alone.
    """
    instruction = _REPORT_INSTRUCTION.format(task_name=task_name)
    block = f"\n\n# openMax Task: {task_name}\n\n{instruction}\n"
    claude_md = Path(cwd) / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "openMax Task:" in existing:
            return
        claude_md.write_text(existing + block, encoding="utf-8")
    else:
        claude_md.write_text(block, encoding="utf-8")
