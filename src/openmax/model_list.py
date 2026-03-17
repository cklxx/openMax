"""Fetch available model IDs from the Anthropic API."""

from __future__ import annotations

import os


def fetch_anthropic_models() -> list[str]:
    """Return model IDs from the Anthropic API using the current API key.

    Returns an empty list on any error (missing key, network failure, etc.).
    """
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        return [m.id for m in client.models.list().data]
    except Exception:
        return []
