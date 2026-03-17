"""Skill installer — deploy openMax as a reusable skill for AI coding agents."""

from __future__ import annotations

from pathlib import Path

_SKILL_FILE = Path(__file__).parent.parent.parent / "skills" / "openmax.md"


def skill_file() -> Path:
    """Return the openmax skill file path."""
    cwd_skill = Path.cwd() / "skills" / "openmax.md"
    return cwd_skill if cwd_skill.exists() else _SKILL_FILE


def install(target_dir: Path) -> Path:
    """Symlink the openmax skill into target_dir. Returns the symlink path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    link = target_dir / "openmax.md"
    src = skill_file()
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(src)
    return link


def project_commands_dir(cwd: str | None = None) -> Path:
    return Path(cwd or ".") / ".claude" / "commands"


def global_commands_dir() -> Path:
    return Path.home() / ".claude" / "commands"
