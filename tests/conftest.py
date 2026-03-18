"""Shared test helpers and fixtures."""

from __future__ import annotations

import time

from openmax.lead_agent import tools as lead_agent_tools

# ── Fake-time infrastructure ────────────────────────────────────────────────
# Patches anyio.sleep and time.monotonic so tool functions that poll/wait
# complete instantly in tests.

_fake_time = 0.0


async def _no_sleep(seconds: float) -> None:
    global _fake_time  # noqa: PLW0603
    _fake_time += seconds


def _fake_monotonic() -> float:
    return _fake_time


def patch_time(monkeypatch) -> None:
    """Patch anyio.sleep and time.monotonic in the lead_agent tools package."""
    global _fake_time  # noqa: PLW0603
    _fake_time = 0.0
    monkeypatch.setattr(lead_agent_tools.anyio, "sleep", _no_sleep)
    monkeypatch.setattr(lead_agent_tools.time, "monotonic", _fake_monotonic)


# ── Polling helper ──────────────────────────────────────────────────────────


def wait_until(predicate, timeout: float = 3.0) -> None:
    """Block until *predicate()* returns truthy, or raise after *timeout* seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")
