"""Skill loader — read skill markdown files and inject into agent prompts."""

from __future__ import annotations

from pathlib import Path

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


def _skills_dir() -> Path:
    """Return the skills directory, falling back to package-relative path."""
    # Check CWD-relative (for installed usage)
    cwd_skills = Path.cwd() / "skills"
    if cwd_skills.is_dir():
        return cwd_skills
    return _SKILLS_DIR


def load_skill(name: str) -> str:
    """Return skill content (without YAML frontmatter) for the given skill name."""
    path = _skills_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill '{name}' not found in {_skills_dir()}")
    text = path.read_text()
    return _strip_frontmatter(text)


def list_skills() -> list[str]:
    """Return names of all available skills."""
    return sorted(p.stem for p in _skills_dir().glob("*.md"))


def inject_skill(prompt: str, skill_name: str, arguments: str = "") -> str:
    """Prepend skill instructions to a prompt, substituting $ARGUMENTS."""
    skill_body = load_skill(skill_name).replace("$ARGUMENTS", arguments)
    return f"## Skill: {skill_name}\n\n{skill_body}\n\n---\n\n{prompt}"


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4 :].lstrip("\n") if end != -1 else text
