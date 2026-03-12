"""Agent registry and config loading for built-in and custom agents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from openmax.adapters import (
    AgentAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    OpenCodeAdapter,
    SubprocessAdapter,
)


class AgentConfigError(RuntimeError):
    """Raised when agent config is invalid."""


@dataclass(frozen=True)
class AgentDefinition:
    """A configured agent entry."""

    name: str
    adapter: AgentAdapter
    source: str
    built_in: bool = False


class AgentRegistry:
    """Registry of available agent adapters."""

    def __init__(self, definitions: Iterable[AgentDefinition]) -> None:
        self._definitions = {definition.name: definition for definition in definitions}

    def names(self) -> list[str]:
        return list(self._definitions)

    def get(self, name: str) -> AgentAdapter | None:
        definition = self._definitions.get(name)
        return definition.adapter if definition else None

    def definitions(self) -> list[AgentDefinition]:
        return list(self._definitions.values())

    def default_agent_name(self) -> str | None:
        if "claude-code" in self._definitions:
            return "claude-code"
        return next(iter(self._definitions), None)

    def with_definition(self, definition: AgentDefinition) -> "AgentRegistry":
        updated = self.definitions()
        updated = [item for item in updated if item.name != definition.name]
        updated.append(definition)
        return AgentRegistry(updated)


def built_in_agent_registry() -> AgentRegistry:
    """Return the built-in agent registry."""
    return AgentRegistry(
        [
            AgentDefinition(
                name="claude-code",
                adapter=ClaudeCodeAdapter(),
                source="built-in",
                built_in=True,
            ),
            AgentDefinition(
                name="codex",
                adapter=CodexAdapter(),
                source="built-in",
                built_in=True,
            ),
            AgentDefinition(
                name="opencode",
                adapter=OpenCodeAdapter(),
                source="built-in",
                built_in=True,
            ),
            AgentDefinition(
                name="generic",
                adapter=SubprocessAdapter("generic", ["claude"]),
                source="built-in",
                built_in=True,
            ),
        ]
    )


def load_agent_registry(cwd: str | None = None) -> AgentRegistry:
    """Load built-in agents plus any configured custom agents."""
    registry = built_in_agent_registry()
    for path, required in _candidate_config_paths(cwd):
        if not path.exists():
            if required:
                raise AgentConfigError(f"Agent config file not found: {path}")
            continue
        registry = _merge_config_file(registry, path)
    return registry


def _candidate_config_paths(cwd: str | None) -> list[tuple[Path, bool]]:
    paths: list[tuple[Path, bool]] = []
    global_path = Path.home() / ".config" / "openmax" / "agents.toml"
    paths.append((global_path, False))
    if cwd:
        paths.append((Path(cwd) / ".openmax" / "agents.toml", False))
    env_path = os.environ.get("OPENMAX_AGENTS_FILE")
    if env_path:
        explicit = Path(env_path).expanduser()
        if cwd and not explicit.is_absolute():
            explicit = Path(cwd) / explicit
        paths.append((explicit, True))
    return paths


def _merge_config_file(registry: AgentRegistry, path: Path) -> AgentRegistry:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise AgentConfigError(f"Invalid TOML in {path}: {exc}") from exc

    agents_table = raw.get("agents", {})
    if not isinstance(agents_table, dict):
        raise AgentConfigError(f"Invalid agent config in {path}: [agents] must be a table")

    merged = registry
    for name, config in agents_table.items():
        definition = _definition_from_config(name, config, path)
        merged = merged.with_definition(definition)
    return merged


def _definition_from_config(name: str, config: object, path: Path) -> AgentDefinition:
    if not isinstance(config, dict):
        raise AgentConfigError(f"Invalid config for agent '{name}' in {path}: must be a table")

    command = config.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: command must be a non-empty string array"
        )

    interactive = config.get("interactive", True)
    if not isinstance(interactive, bool):
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: interactive must be true or false"
        )

    startup_delay = config.get("startup_delay", 3.0)
    if not isinstance(startup_delay, (int, float)) or startup_delay < 0:
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: startup_delay must be >= 0"
        )

    if not interactive and not any("{prompt}" in item or "{prompt_sh}" in item for item in command):
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: non-interactive commands must include "
            "{prompt} or {prompt_sh}"
        )

    adapter = SubprocessAdapter(
        name=name,
        command_template=command,
        is_interactive=interactive,
        startup_delay=float(startup_delay),
    )
    return AgentDefinition(name=name, adapter=adapter, source=str(path), built_in=False)
