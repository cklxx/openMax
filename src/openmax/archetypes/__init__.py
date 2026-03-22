from __future__ import annotations

import functools
import logging
import pathlib

from openmax.archetypes._base import (
    Archetype,
    SubtaskTemplate,
    classify_task,
    match_archetype,
)
from openmax.archetypes.api_service import ARCHETYPE as _api
from openmax.archetypes.cli_tool import ARCHETYPE as _cli
from openmax.archetypes.library import ARCHETYPE as _lib
from openmax.archetypes.refactor import ARCHETYPE as _refactor
from openmax.archetypes.web_app import ARCHETYPE as _web

__all__ = [
    "Archetype",
    "BUILT_IN_ARCHETYPES",
    "SubtaskTemplate",
    "classify_task",
    "format_archetype_context",
    "format_subtask_hints",
    "get_all_archetypes",
    "load_custom_archetypes",
    "match_archetype",
]

log = logging.getLogger(__name__)

BUILT_IN_ARCHETYPES: list[Archetype] = [_web, _cli, _api, _lib, _refactor]


def _parse_yaml_archetype(data: dict) -> Archetype:
    templates = [SubtaskTemplate(**t) for t in data.get("subtask_templates", [])]
    return Archetype(
        name=data["name"],
        display_name=data.get("display_name", data["name"]),
        description=data.get("description", ""),
        subtask_templates=templates,
        planning_hints=data.get("planning_hints", []),
        anti_patterns=data.get("anti_patterns", []),
    )


def _load_yaml_file(path: pathlib.Path) -> Archetype | None:
    try:
        import yaml  # noqa: F811
    except ImportError:
        log.debug("PyYAML not installed — skipping custom archetype %s", path)
        return None
    return _try_parse_yaml(path, yaml)


def _try_parse_yaml(path: pathlib.Path, yaml: object) -> Archetype | None:
    try:
        data = yaml.safe_load(path.read_text())  # type: ignore[union-attr]
        return _parse_yaml_archetype(data)
    except Exception:
        log.warning("Failed to parse custom archetype: %s", path, exc_info=True)
        return None


def load_custom_archetypes(cwd: str) -> list[Archetype]:
    """Load custom archetypes from .openmax/archetypes/*.yaml."""
    arch_dir = pathlib.Path(cwd) / ".openmax" / "archetypes"
    if not arch_dir.is_dir():
        return []
    return _collect_yaml_archetypes(arch_dir)


def _collect_yaml_archetypes(arch_dir: pathlib.Path) -> list[Archetype]:
    results: list[Archetype] = []
    for path in sorted(arch_dir.glob("*.yaml")):
        arch = _load_yaml_file(path)
        if arch is not None:
            results.append(arch)
    return results


@functools.lru_cache(maxsize=8)
def get_all_archetypes(cwd: str) -> list[Archetype]:
    """Return built-in + custom archetypes (cached per cwd)."""
    return BUILT_IN_ARCHETYPES + load_custom_archetypes(cwd)


def format_archetype_context(archetype: Archetype, task: str) -> str:
    """Format archetype info for lead agent prompt injection."""
    lines = [
        f"## Archetype: {archetype.display_name}",
        f"**Task:** {task}",
        f"**Description:** {archetype.description}",
        "",
        _format_section("Planning hints", archetype.planning_hints),
        _format_section("Anti-patterns to avoid", archetype.anti_patterns),
    ]
    return "\n".join(lines)


def _format_section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    bullets = "\n".join(f"- {item}" for item in items)
    return f"### {title}\n{bullets}\n"


def format_subtask_hints(archetype: Archetype) -> str:
    """Format subtask templates as hints for dispatch prompts."""
    if not archetype.subtask_templates:
        return ""
    lines = ["## Suggested subtask breakdown:"]
    for t in archetype.subtask_templates:
        lines.append(_format_template_line(t))
    return "\n".join(lines)


def _format_template_line(t: SubtaskTemplate) -> str:
    deps = f" (after: {', '.join(t.dependencies)})" if t.dependencies else ""
    return f"- **{t.name}**: {t.description} (~{t.estimated_minutes}m){deps}"
