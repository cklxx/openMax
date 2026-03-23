"""Persistent openMax user config (~/.openmax/config.json)."""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / ".openmax" / "config.json"

_cached_config: dict | None = None
_cached_mtime: float = 0.0


def _load() -> dict:
    global _cached_config, _cached_mtime
    try:
        mt = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cached_config is not None and mt == _cached_mtime:
        return _cached_config
    try:
        _cached_config = json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        _cached_config = {}
    _cached_mtime = mt
    return _cached_config


def _save(data: dict) -> None:
    global _cached_config, _cached_mtime
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))
    _cached_config = data
    _cached_mtime = _CONFIG_PATH.stat().st_mtime


def get_model() -> str | None:
    return _load().get("model")


def set_model(model: str) -> None:
    data = _load()
    data["model"] = model
    _save(data)


# Known Claude models — used when ANTHROPIC_API_KEY is not available (e.g. OAuth auth).
_KNOWN_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-5",
    "claude-sonnet-4-5-20251001",
]


def fetch_anthropic_models() -> list[str]:
    """Return model IDs. Tries Anthropic API first; falls back to built-in list."""
    try:
        import anthropic

        ids = [m.id for m in anthropic.Anthropic().models.list().data]
        if ids:
            return ids
    except Exception:
        pass
    return _KNOWN_MODELS
