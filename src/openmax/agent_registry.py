"""Agent registry and config loading for built-in and custom agents."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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

    def with_definition(self, definition: AgentDefinition) -> 'AgentRegistry':
        updated = self.definitions()
        updated = [item for item in updated if item.name != definition.name]
        updated.append(definition)
        return AgentRegistry(updated)


@lru_cache(maxsize=1)
def _built_in_definitions() -> tuple[AgentDefinition, ...]:
    return (
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
    )


def built_in_agent_registry() -> AgentRegistry:
    """Return the built-in agent registry."""
    return AgentRegistry(_built_in_definitions())


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
    global_path = Path.home() / '.config' / 'openmax' / 'agents.toml'
    paths.append((global_path, False))
    if cwd:
        paths.append((Path(cwd) / '.openmax' / 'agents.toml', False))
    env_path = os.environ.get('OPENMAX_AGENTS_FILE')
    if env_path:
        explicit = Path(env_path).expanduser()
        if cwd and not explicit.is_absolute():
            explicit = Path(cwd) / explicit
        paths.append((explicit, True))
    return paths


def _merge_config_file(registry: AgentRegistry, path: Path) -> AgentRegistry:
    try:
        raw = tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as exc:
        raise AgentConfigError(f"Invalid TOML in {path}: {exc}") from exc

    agents_table = raw.get('agents', {})
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
    is_valid = (
        isinstance(command, list) and command and all(isinstance(item, str) for item in command)
    )
    if not is_valid:
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

    env = _resolve_agent_env(name, config.get("env", {}), path)

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
        env=env,
    )
    return AgentDefinition(name=name, adapter=adapter, source=str(path), built_in=False)


def _resolve_agent_env(name: str, raw_env: object, path: Path) -> dict[str, str]:
    if not isinstance(raw_env, dict):
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: env must be a table"
        )

    resolved: dict[str, str] = {}
    for key, value in raw_env.items():
        if not isinstance(key, str):
            raise AgentConfigError(
                f"Invalid config for agent '{name}' in {path}: env keys must be strings"
            )
        if isinstance(value, str):
            if key in ("CLAUDE_CODE_SETUP_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
                raise AgentConfigError(
                    f"Invalid config for agent '{name}' in {path}: env.{key} must come from an environment variable reference"
                )
            resolved[key] = value
            continue
        if isinstance(value, dict):
            from_env = value.get("from_env")
            legacy_env = value.get("env")
            source_env = None
            if len(value) == 1 and isinstance(from_env, str) and from_env:
                source_env = from_env
            elif len(value) == 1 and isinstance(legacy_env, str) and legacy_env:
                source_env = legacy_env
            if not source_env:
                raise AgentConfigError(
                    f"Invalid config for agent '{name}' in {path}: env.{key} must be a string or {{ from_env = 'NAME' }}"
                )
            env_value = os.environ.get(source_env)
            if env_value is None:
                raise AgentConfigError(
                    f"Invalid config for agent '{name}' in {path}: missing environment variable {source_env} for env.{key}"
                )
            resolved[key] = env_value
            continue
        raise AgentConfigError(
            f"Invalid config for agent '{name}' in {path}: env.{key} must be a string or {{ from_env = 'NAME' }}"
        )
    return resolved
