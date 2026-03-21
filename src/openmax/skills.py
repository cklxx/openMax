"""Skill installer — deploy openMax skills as reusable commands for AI coding agents."""

from __future__ import annotations

from pathlib import Path

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
_SKILL_FILES: dict[str, str] = {"openmax": "openmax.md", "codex": "codex.md"}


def _resolve_skill_path(filename: str) -> Path:
    cwd_skill = Path.cwd() / "skills" / filename
    return cwd_skill if cwd_skill.exists() else _SKILLS_DIR / filename


def install(target_dir: Path, skill_name: str | None = None) -> list[Path]:
    """Install one or all skills into target_dir. Returns created symlink paths."""
    target_dir.mkdir(parents=True, exist_ok=True)
    names = [skill_name] if skill_name else list(_SKILL_FILES)
    links: list[Path] = []
    for name in names:
        filename = _SKILL_FILES.get(name)
        if filename is None:
            continue
        src = _resolve_skill_path(filename)
        if not src.exists():
            continue
        link = target_dir / filename
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(src)
        links.append(link)
    return links


def skill_file() -> Path:
    """Return the openmax skill file path (legacy compat)."""
    return _resolve_skill_path("openmax.md")


def project_commands_dir(cwd: str | None = None) -> Path:
    return Path(cwd or ".") / ".claude" / "commands"


def global_commands_dir() -> Path:
    return Path.home() / ".claude" / "commands"
