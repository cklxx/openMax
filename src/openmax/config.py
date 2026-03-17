"""Persistent openMax user config (~/.openmax/config.json)."""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / ".openmax" / "config.json"


def _load() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


def get_model() -> str | None:
    return _load().get("model")


def set_model(model: str) -> None:
    data = _load()
    data["model"] = model
    _save(data)


def fetch_anthropic_models() -> list[str]:
    """Return model IDs from the Anthropic API. Empty list on any error."""
    try:
        import anthropic

        return [m.id for m in anthropic.Anthropic().models.list().data]
    except Exception:
        return []
