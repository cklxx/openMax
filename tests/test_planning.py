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
from openmax.lead_agent.tools._planning import (
    _get_shared_dirs,
    predict_conflicts,
    submit_plan,
)


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


# --- Unit tests for conflict prediction ---


class TestGetSharedDirs:
    def test_no_overlap(self):
        assert _get_shared_dirs(["src/a.py"], ["lib/b.py"]) == set()

    def test_same_directory(self):
        assert _get_shared_dirs(["src/a.py"], ["src/b.py"]) == {"src"}

    def test_root_files(self):
        assert _get_shared_dirs(["a.py"], ["b.py"]) == {"."}

    def test_nested_dirs(self):
        result = _get_shared_dirs(["src/openmax/tools/a.py"], ["src/openmax/tools/b.py"])
        assert result == {"src/openmax/tools"}


class TestPredictConflicts:
    def test_no_conflicts_no_warnings(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["lib/b.py"]},
        ]
        result = predict_conflicts(subtasks, [["t1", "t2"]], {})
        assert result == []

    def test_high_rate_produces_warning(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["src/b.py"]},
        ]
        rates = {"src": 0.8}
        result = predict_conflicts(subtasks, [["t1", "t2"]], rates)
        assert len(result) == 1
        assert "t1 and t2" in result[0]
        assert "src" in result[0]
        assert "80%" in result[0]

    def test_low_rate_no_warning(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["src/b.py"]},
        ]
        rates = {"src": 0.3}
        result = predict_conflicts(subtasks, [["t1", "t2"]], rates)
        assert result == []

    def test_threshold_boundary_no_warning_at_exact(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["src/b.py"]},
        ]
        rates = {"src": 0.5}
        result = predict_conflicts(subtasks, [["t1", "t2"]], rates)
        assert result == []

    def test_multiple_groups_checked(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["src/b.py"]},
            {"name": "t3", "files": ["lib/c.py"]},
            {"name": "t4", "files": ["lib/d.py"]},
        ]
        rates = {"src": 0.9, "lib": 0.7}
        result = predict_conflicts(subtasks, [["t1", "t2"], ["t3", "t4"]], rates)
        assert len(result) == 2

    def test_missing_files_field(self):
        subtasks = [
            {"name": "t1"},
            {"name": "t2", "files": ["src/b.py"]},
        ]
        result = predict_conflicts(subtasks, [["t1", "t2"]], {"src": 0.9})
        assert result == []

    def test_custom_threshold(self):
        subtasks = [
            {"name": "t1", "files": ["src/a.py"]},
            {"name": "t2", "files": ["src/b.py"]},
        ]
        rates = {"src": 0.3}
        result = predict_conflicts(subtasks, [["t1", "t2"]], rates, threshold=0.2)
        assert len(result) == 1


# --- Integration: conflict warnings in submit_plan ---


@pytest.mark.asyncio()
async def test_submit_plan_includes_conflict_warnings(_bound_runtime_no_confirm):
    """High conflict rate dirs produce warnings in submit_plan response."""
    from openmax.stats import SessionStats

    stats = SessionStats(merge_conflict_rate_by_dir={"src": 0.9})
    args = {
        "subtasks": [
            {"name": "t1", "description": "Do T1", "files": ["src/a.py"]},
            {"name": "t2", "description": "Do T2", "files": ["src/b.py"]},
        ],
        "rationale": "Two parallel tasks",
        "parallel_groups": [["t1", "t2"]],
    }
    with patch("openmax.lead_agent.tools._planning.load_stats", return_value=stats):
        result = await submit_plan.handler(args)

    content = result["content"][0]["text"]
    assert "conflict_warnings" in content
    assert "src" in content


@pytest.mark.asyncio()
async def test_submit_plan_no_conflict_warnings_when_clean(
    _bound_runtime_no_confirm,
):
    """No conflict warnings when dirs have low conflict rates."""
    from openmax.stats import SessionStats

    stats = SessionStats(merge_conflict_rate_by_dir={"src": 0.1})
    args = {
        "subtasks": [
            {"name": "t1", "description": "Do T1", "files": ["src/a.py"]},
            {"name": "t2", "description": "Do T2", "files": ["src/b.py"]},
        ],
        "rationale": "Two parallel tasks",
        "parallel_groups": [["t1", "t2"]],
    }
    with patch("openmax.lead_agent.tools._planning.load_stats", return_value=stats):
        result = await submit_plan.handler(args)

    content = result["content"][0]["text"]
    assert "conflict_warnings" not in content
