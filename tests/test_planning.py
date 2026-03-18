"""Tests for plan submission and confirmation flow."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.lead_agent.tools._planning import submit_plan


@dataclass
class FakeDashboard:
    stopped: bool = False
    started: bool = False

    def stop(self) -> None:
        self.stopped = True

    def start(self) -> None:
        self.started = True


def _make_runtime(plan_confirm: bool = True) -> LeadAgentRuntime:
    return LeadAgentRuntime(
        cwd="/tmp/test",
        plan=SimpleNamespace(subtasks=[]),
        pane_mgr=SimpleNamespace(),
        dashboard=FakeDashboard(),
        plan_confirm=plan_confirm,
    )


def _basic_plan_args() -> dict[str, Any]:
    return {
        "subtasks": [
            {"name": "task-a", "description": "Do A", "files": ["a.py"]},
            {"name": "task-b", "description": "Do B", "files": ["b.py"]},
        ],
        "rationale": "Split into two independent tasks",
        "parallel_groups": [["task-a", "task-b"]],
    }


@pytest.fixture()
def _bound_runtime_confirm():
    runtime = _make_runtime(plan_confirm=True)
    token = bind_lead_agent_runtime(runtime)
    yield runtime
    reset_lead_agent_runtime(token)


@pytest.fixture()
def _bound_runtime_no_confirm():
    runtime = _make_runtime(plan_confirm=False)
    token = bind_lead_agent_runtime(runtime)
    yield runtime
    reset_lead_agent_runtime(token)


@pytest.mark.asyncio()
async def test_submit_plan_accepted_when_user_approves(_bound_runtime_confirm):
    with patch("builtins.input", return_value="y"):
        result = await submit_plan.handler(_basic_plan_args())

    content = result["content"][0]["text"]
    assert '"accepted"' in content


@pytest.mark.asyncio()
async def test_submit_plan_accepted_on_empty_input(_bound_runtime_confirm):
    with patch("builtins.input", return_value=""):
        result = await submit_plan.handler(_basic_plan_args())

    content = result["content"][0]["text"]
    assert '"accepted"' in content


@pytest.mark.asyncio()
async def test_submit_plan_revision_requested_on_feedback(_bound_runtime_confirm):
    with patch("builtins.input", return_value="add a third task for tests"):
        result = await submit_plan.handler(_basic_plan_args())

    content = result["content"][0]["text"]
    assert "revision_requested" in content
    assert "add a third task for tests" in content


@pytest.mark.asyncio()
async def test_submit_plan_skips_confirmation_when_disabled(_bound_runtime_no_confirm):
    result = await submit_plan.handler(_basic_plan_args())

    content = result["content"][0]["text"]
    assert '"accepted"' in content


@pytest.mark.asyncio()
async def test_submit_plan_stops_and_restarts_dashboard(_bound_runtime_confirm):
    runtime = _bound_runtime_confirm
    dashboard = runtime.dashboard

    with patch("builtins.input", return_value="yes"):
        await submit_plan.handler(_basic_plan_args())

    assert dashboard.stopped
    assert dashboard.started


@pytest.mark.asyncio()
async def test_submit_plan_handles_eof_as_approval(_bound_runtime_confirm):
    with patch("builtins.input", side_effect=EOFError):
        result = await submit_plan.handler(_basic_plan_args())

    content = result["content"][0]["text"]
    assert '"accepted"' in content
