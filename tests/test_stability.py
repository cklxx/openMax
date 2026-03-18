"""Stability and recoverability tests.

Exercise error paths that production encounters: agent crashes, stuck agents,
dispatch failures, dead-pane caching, session resume with stale tasks, and
verification failures.  All tests run headless — no tmux/kaku needed.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import anyio

from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, AgentRegistry, built_in_agent_registry
from openmax.lead_agent import PlanResult, TaskStatus
from openmax.lead_agent import tools as lead_agent_tools
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.lead_agent.tools._dispatch import _STUCK_THRESHOLD
from openmax.memory import MemoryStore
from openmax.pane_backend import HeadlessPaneBackend, PaneBackendError
from openmax.pane_manager import PaneManager
from openmax.session_runtime import SessionStore, reconcile_resumed_subtasks
from tests.conftest import patch_time, wait_until


def _setup(tmp_path, *, pane_mgr=None, agent_registry=None):
    """Create a LeadAgentRuntime wired to tmp_path stores."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("stab-test", "Goal", str(workspace))
    runtime = LeadAgentRuntime(
        cwd=str(workspace),
        plan=PlanResult(goal="Goal"),
        pane_mgr=pane_mgr or PaneManager(backend=HeadlessPaneBackend()),
        session_store=store,
        session_meta=meta,
        memory_store=MemoryStore(base_dir=tmp_path / "memory"),
        agent_registry=agent_registry or built_in_agent_registry(),
    )
    token = bind_lead_agent_runtime(runtime)
    return runtime, token


def _teardown(token):
    reset_lead_agent_runtime(token)


def _crash_agent_registry() -> AgentRegistry:
    """Agent that exits immediately with code 1."""
    adapter = SubprocessAdapter(
        name="crash-agent",
        command_template=[sys.executable, "-c", "import sys; sys.exit(1)"],
        is_interactive=False,
    )
    return AgentRegistry([AgentDefinition(name="crash-agent", adapter=adapter, source="test")])


def _slow_agent_registry() -> AgentRegistry:
    """Agent that prints a line then sleeps long enough for reads."""
    script = "import time; print('READY', flush=True); time.sleep(30)"
    adapter = SubprocessAdapter(
        name="slow-agent",
        command_template=[sys.executable, "-u", "-c", script],
        is_interactive=False,
    )
    return AgentRegistry([AgentDefinition(name="slow-agent", adapter=adapter, source="test")])


# ── Tests ────────────────────────────────────────────────────────────────────


def test_agent_crash_detected_as_exited(monkeypatch, tmp_path):
    """Dispatch an agent that exits immediately; read_pane_output reports exited=True."""
    runtime, token = _setup(tmp_path, agent_registry=_crash_agent_registry())
    patch_time(monkeypatch)
    try:
        dispatch_result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "crash-task", "agent_type": "crash-agent", "prompt": "do stuff"},
        )
        text = json.loads(dispatch_result["content"][0]["text"])
        assert text["status"] == "dispatched"
        pane_id = text["pane_id"]

        # Wait for process to actually exit
        wait_until(lambda: not runtime.pane_mgr.is_pane_alive(pane_id))

        read_result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": pane_id},
        )
        data = json.loads(read_result["content"][0]["text"])
        assert data["exited"] is True
        assert data["stuck"] is False
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_dead_pane_cached_output_readable(monkeypatch, tmp_path):
    """After a pane dies, PaneManager.get_text returns cached output."""
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())
    runtime, token = _setup(
        tmp_path,
        pane_mgr=pane_mgr,
        agent_registry=AgentRegistry(
            [
                AgentDefinition(
                    name="echo-agent",
                    adapter=SubprocessAdapter(
                        name="echo-agent",
                        command_template=[
                            sys.executable,
                            "-u",
                            "-c",
                            "print('HELLO_CACHE_TEST', flush=True)",
                        ],
                        is_interactive=False,
                    ),
                    source="test",
                )
            ]
        ),
    )
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "echo-task", "agent_type": "echo-agent", "prompt": "echo"},
        )
        pane_id = runtime.plan.subtasks[0].pane_id
        # Wait for output to appear then process to die
        wait_until(lambda: "HELLO_CACHE_TEST" in pane_mgr.get_text(pane_id))
        wait_until(lambda: not pane_mgr.is_pane_alive(pane_id))

        # Cached output should still be accessible
        cached = pane_mgr.get_text(pane_id)
        assert "HELLO_CACHE_TEST" in cached
    finally:
        pane_mgr.cleanup_all()
        _teardown(token)


def test_stuck_detection_after_identical_reads(monkeypatch, tmp_path):
    """read_pane_output reports stuck=True after STUCK_THRESHOLD identical reads."""
    runtime, token = _setup(tmp_path, agent_registry=_slow_agent_registry())
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "stuck-task", "agent_type": "slow-agent", "prompt": "wait"},
        )
        pane_id = runtime.plan.subtasks[0].pane_id
        wait_until(lambda: "READY" in runtime.pane_mgr.get_text(pane_id))

        # Read N times — output is identical each time ("READY\n" + sleep)
        last_data = None
        for i in range(_STUCK_THRESHOLD):
            result = anyio.run(
                lead_agent_tools.read_pane_output.handler,
                {"pane_id": pane_id},
            )
            last_data = json.loads(result["content"][0]["text"])
            if i < _STUCK_THRESHOLD - 1:
                assert last_data["stuck"] is False

        # After STUCK_THRESHOLD identical reads, stuck should be True
        assert last_data["stuck"] is True
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_send_text_to_dead_pane_returns_error(monkeypatch, tmp_path):
    """Sending text to a dead pane returns an error, not a crash."""
    runtime, token = _setup(tmp_path, agent_registry=_crash_agent_registry())
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "dead-send", "agent_type": "crash-agent", "prompt": "x"},
        )
        pane_id = runtime.plan.subtasks[0].pane_id
        wait_until(lambda: not runtime.pane_mgr.is_pane_alive(pane_id))

        result = anyio.run(
            lead_agent_tools.send_text_to_pane.handler,
            {"pane_id": pane_id, "text": "hello"},
        )
        text = result["content"][0]["text"]
        assert "Error" in text
        assert "no longer exists" in text
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_dispatch_failure_returns_error_response(monkeypatch, tmp_path):
    """When the pane backend fails to spawn, dispatch_agent returns an error response."""

    class FailingBackend(HeadlessPaneBackend):
        def spawn_window(self, command, cwd=None, env=None):
            raise PaneBackendError("backend is down")

        def split_pane(self, target, direction, command, cwd=None, env=None):
            raise PaneBackendError("backend is down")

    pane_mgr = PaneManager(backend=FailingBackend())
    runtime, token = _setup(
        tmp_path,
        pane_mgr=pane_mgr,
        agent_registry=_crash_agent_registry(),
    )
    patch_time(monkeypatch)
    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "fail-task", "agent_type": "crash-agent", "prompt": "x"},
        )
        data = json.loads(result["content"][0]["text"])
        assert data["status"] == "error"
        assert "error" in data
        assert "remediation" in data
    finally:
        _teardown(token)


def test_mark_task_done_on_dead_pane(monkeypatch, tmp_path):
    """mark_task_done succeeds even after the agent pane has died."""
    runtime, token = _setup(tmp_path, agent_registry=_crash_agent_registry())
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "done-dead", "agent_type": "crash-agent", "prompt": "x"},
        )
        pane_id = runtime.plan.subtasks[0].pane_id
        wait_until(lambda: not runtime.pane_mgr.is_pane_alive(pane_id))

        result = anyio.run(
            lead_agent_tools.mark_task_done.handler,
            {"task_name": "done-dead", "notes": "completed despite crash"},
        )
        text = result["content"][0]["text"]
        assert "done" in text.lower()
        assert runtime.plan.subtasks[0].status == TaskStatus.DONE
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_resume_resets_stale_running_tasks(tmp_path):
    """reconcile_resumed_subtasks resets running tasks with dead panes to pending."""
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())
    plan = SimpleNamespace(
        goal="test",
        subtasks=[
            SimpleNamespace(name="alive-task", status="running", pane_id=9999),
            SimpleNamespace(name="dead-task", status="running", pane_id=8888),
            SimpleNamespace(name="done-task", status="done", pane_id=7777),
        ],
    )
    # Neither pane 9999 nor 8888 exist in headless backend → both should reset
    reset = reconcile_resumed_subtasks(plan, pane_mgr)
    assert "alive-task" in reset
    assert "dead-task" in reset
    assert "done-task" not in reset
    assert plan.subtasks[0].status == "pending"
    assert plan.subtasks[1].status == "pending"
    assert plan.subtasks[2].status == "done"


def test_verification_failure_returns_dispatch_hint(monkeypatch, tmp_path):
    """run_verification on a failing command returns structured fail with dispatch_hint."""
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "headless")
    runtime, token = _setup(tmp_path)
    try:
        result = anyio.run(
            lead_agent_tools.run_verification.handler,
            {
                "check_type": "lint",
                "command": "ls /nonexistent_path_for_test_2>/dev/null; false",
                "timeout": 10,
            },
        )
        data = json.loads(result["content"][0]["text"])
        assert data["status"] == "fail"
        assert data["exit_code"] == 1
        assert "dispatch_hint" in data
        assert "false" in data["dispatch_hint"]
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_verification_pass_returns_clean_result(monkeypatch, tmp_path):
    """run_verification on a passing command returns status=pass with no dispatch_hint."""
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "headless")
    runtime, token = _setup(tmp_path)
    try:
        result = anyio.run(
            lead_agent_tools.run_verification.handler,
            {
                "check_type": "test",
                "command": "echo PASS",
                "timeout": 10,
            },
        )
        data = json.loads(result["content"][0]["text"])
        assert data["status"] == "pass"
        assert data["exit_code"] == 0
        assert "dispatch_hint" not in data
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_read_pane_output_all_panes_summary(monkeypatch, tmp_path):
    """read_pane_output with pane_id=-1 returns a summary of all managed panes."""
    runtime, token = _setup(tmp_path, agent_registry=_slow_agent_registry())
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "summary-task", "agent_type": "slow-agent", "prompt": "x"},
        )
        result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": -1},
        )
        data = json.loads(result["content"][0]["text"])
        assert "total_panes" in data or "total_windows" in data
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_concurrent_dispatch_creates_multiple_panes(monkeypatch, tmp_path):
    """Dispatching two agents creates two separate panes in the same window."""
    runtime, token = _setup(tmp_path, agent_registry=_slow_agent_registry())
    patch_time(monkeypatch)
    try:
        for name in ("task-a", "task-b"):
            anyio.run(
                lead_agent_tools.dispatch_agent.handler,
                {"task_name": name, "agent_type": "slow-agent", "prompt": "wait"},
            )
        assert len(runtime.plan.subtasks) == 2
        pane_ids = {st.pane_id for st in runtime.plan.subtasks}
        assert len(pane_ids) == 2
        # Both should share the same window
        win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
        assert win is not None
        assert len(win.pane_ids) == 2
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)


def test_duplicate_task_name_deduplication(monkeypatch, tmp_path):
    """Dispatching two tasks with the same name auto-deduplicates the second."""
    runtime, token = _setup(tmp_path, agent_registry=_slow_agent_registry())
    patch_time(monkeypatch)
    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "dup", "agent_type": "slow-agent", "prompt": "first"},
        )
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "dup", "agent_type": "slow-agent", "prompt": "second"},
        )
        names = [st.name for st in runtime.plan.subtasks]
        assert "dup" in names
        assert "dup-2" in names
    finally:
        runtime.pane_mgr.cleanup_all()
        _teardown(token)
