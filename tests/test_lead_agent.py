from __future__ import annotations

import time
from types import SimpleNamespace

import anyio

from openmax import lead_agent
from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import LeadAgentStartupError, PlanResult, SubTask, TaskStatus
from openmax.memory_system import MemoryStore
from openmax.session_runtime import (
    LeadAgentRuntime,
    SessionStore,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)


class DummyPaneManager:
    def __init__(self) -> None:
        self.windows: dict[int, SimpleNamespace] = {}
        self.sent: list[tuple[int, str]] = []
        self.created_commands: list[list[str]] = []

    def create_window(self, command, purpose, agent_type, title, cwd, env=None):
        self.created_commands.append(command)
        self.windows[7] = SimpleNamespace(pane_ids=[101])
        return SimpleNamespace(pane_id=101, window_id=7)

    def add_pane(self, window_id, command, purpose, agent_type, cwd, env=None):
        self.created_commands.append(command)
        self.windows[window_id].pane_ids.append(102)
        return SimpleNamespace(pane_id=102, window_id=window_id)

    def send_text(self, pane_id, text):
        self.sent.append((pane_id, text))

    def get_text(self, pane_id):
        return f"pane {pane_id} output\n$ "

    def refresh_states(self):
        return None

    def summary(self):
        return {"total_windows": len(self.windows), "done": 0}


_fake_time = 0.0


async def _no_sleep(seconds: float) -> None:
    global _fake_time  # noqa: PLW0603
    _fake_time += seconds


def _fake_monotonic() -> float:
    return _fake_time


def _setup_session(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("lead-test", "Goal", str(tmp_path))
    memory_store = MemoryStore(base_dir=tmp_path / "memory")
    runtime = LeadAgentRuntime(
        cwd=str(tmp_path),
        plan=PlanResult(goal="Goal"),
        pane_mgr=DummyPaneManager(),
        session_store=store,
        session_meta=meta,
        memory_store=memory_store,
        agent_registry=built_in_agent_registry(),
    )
    token = bind_lead_agent_runtime(runtime)
    return runtime, token, store, meta, memory_store


def _teardown_session(token):
    reset_lead_agent_runtime(token)


class FailingClaudeClient:
    def __init__(self, options, error: Exception, fail_stage: str) -> None:
        self.options = options
        self._error = error
        self._fail_stage = fail_stage

    async def __aenter__(self):
        if self._fail_stage == "enter":
            raise self._error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def query(self, prompt):
        if self._fail_stage == "query":
            raise self._error

    async def receive_response(self):
        if self._fail_stage == "stream":
            raise self._error
        if False:
            yield None


def _patch_time(monkeypatch):
    """Patch both anyio.sleep and time.monotonic so _wait_for_pane_ready exits fast."""
    global _fake_time  # noqa: PLW0603
    _fake_time = 0.0
    monkeypatch.setattr(lead_agent.anyio, "sleep", _no_sleep)
    monkeypatch.setattr(lead_agent.time, "monotonic", _fake_monotonic)


def test_dispatch_agent_persists_event(monkeypatch, tmp_path):
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "generic", "prompt": "Implement API"},
    )

    events = store.load_events("lead-test")
    assert any(event.event_type == "tool.dispatch_agent" for event in events)
    assert runtime.plan.subtasks == [
        SubTask(
            name="API",
            agent_type="generic",
            prompt="Implement API",
            status=TaskStatus.RUNNING,
            pane_id=101,
        )
    ]
    assert runtime.pane_mgr.sent == [(101, "Implement API")]

    _teardown_session(token)


def test_format_tool_use_humanizes_all_openmax_tools():
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__dispatch_agent",
            {"task_name": "API routes", "agent_type": "codex"},
        )
        == "Starting agent for API routes via codex"
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__get_agent_recommendations",
            {"task": "Refactor API routes"},
        )
        == "Checking best agent for Refactor API routes"
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__read_pane_output",
            {"pane_id": 12},
        )
        == "Checking progress in pane 12"
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__send_text_to_pane",
            {"pane_id": 12, "text": "Please rerun the failing tests with logs"},
        )
        == "Sending follow-up to pane 12: Please rerun the failing tests with logs"
    )
    assert (
        lead_agent._format_tool_use("mcp__openmax__list_managed_panes", {})
        == "Reviewing active panes"
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__mark_task_done",
            {"task_name": "API routes"},
        )
        == "Marking API routes done"
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__record_phase_anchor",
            {
                "phase": "plan",
                "summary": "Defined the delivery plan and split the work.",
                "completion_pct": 20,
            },
        )
        == "Saving planning checkpoint (20%): Defined the delivery plan and split the work."
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__remember_learning",
            {"lesson": "Prefer codex when editing Python test suites."},
        )
        == "Saving reusable lesson: Prefer codex when editing Python test suites."
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__report_completion",
            {"completion_pct": 100, "notes": "Everything finished and verified."},
        )
        == "Publishing completion update (100%): Everything finished and verified."
    )
    assert (
        lead_agent._format_tool_use(
            "mcp__openmax__wait",
            {"seconds": 45},
        )
        == "Waiting 45s before the next check"
    )


def test_dispatch_agent_enforces_allowed_agents(monkeypatch, tmp_path):
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)
    runtime.allowed_agents = ["codex"]

    result = anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "claude-code", "prompt": "Implement API"},
    )

    # Should have fallen back to codex (first in allowed list)
    subtask = runtime.plan.subtasks[0]
    assert subtask.agent_type == "codex"
    import json

    dispatched = json.loads(result["content"][0]["text"])
    assert dispatched["agent_type"] == "codex"

    runtime.allowed_agents = None
    _teardown_session(token)


def test_report_completion_writes_report_and_anchor(tmp_path):
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    runtime.plan.subtasks.append(
        SubTask(
            name="API",
            agent_type="codex",
            prompt="Implement API",
            status=TaskStatus.DONE,
            pane_id=42,
        )
    )

    anyio.run(
        lead_agent.report_completion.handler,
        {"completion_pct": 100, "notes": "Everything finished"},
    )

    events = store.load_events("lead-test")
    assert [event.event_type for event in events][-2:] == ["tool.report_completion", "phase.anchor"]
    refreshed_meta = store.load_meta(meta.session_id)
    assert refreshed_meta.latest_phase == "report"
    memories = memory_store.load_entries(str(tmp_path))
    assert memories
    assert memories[-1].kind == "run_summary"

    _teardown_session(token)


def test_remember_learning_stores_workspace_memory(tmp_path):
    _runtime, token, _store, _meta, memory_store = _setup_session(tmp_path)

    anyio.run(
        lead_agent.remember_learning.handler,
        {
            "lesson": "Prefer codex for API work.",
            "rationale": "It converged fastest in the last run.",
            "confidence": 9,
        },
    )

    memories = memory_store.load_entries(str(tmp_path))
    assert memories
    assert memories[-1].kind == "lesson"
    assert memories[-1].summary == "Prefer codex for API work."

    _teardown_session(token)


def test_get_agent_recommendations_returns_ranked_json(tmp_path):
    _runtime, token, _store, _meta, memory_store = _setup_session(tmp_path)
    memory_store.record_run_summary(
        cwd=str(tmp_path),
        task="Build API endpoints",
        notes="Codex completed the API endpoints cleanly.",
        completion_pct=100,
        subtasks=[
            {
                "name": "API",
                "agent_type": "codex",
                "status": "done",
                "prompt": "Update src/api/routes.py",
            }
        ],
        anchors=[{"summary": "API work succeeded in src/api/routes.py"}],
    )

    result = anyio.run(
        lead_agent.get_agent_recommendations.handler,
        {"task": "Refactor API endpoints"},
    )

    assert "codex" in result["content"][0]["text"]

    _teardown_session(token)


def test_dispatch_agent_uses_configured_custom_agent(monkeypatch, tmp_path):
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    sleep_calls: list[float] = []

    global _fake_time  # noqa: PLW0603
    _fake_time = 0.0

    async def fake_sleep(seconds: float) -> None:
        global _fake_time  # noqa: PLW0603
        _fake_time += seconds
        sleep_calls.append(seconds)

    monkeypatch.setattr(lead_agent.anyio, "sleep", fake_sleep)
    monkeypatch.setattr(lead_agent.time, "monotonic", _fake_monotonic)
    runtime.agent_registry = built_in_agent_registry().with_definition(
        AgentDefinition(
            name="remote-codex",
            adapter=SubprocessAdapter(
                "remote-codex",
                ["ssh", "devbox", "bash", "-lc", "cd {cwd_sh} && codex"],
                startup_delay=9,
            ),
            source="test",
            built_in=False,
        )
    )

    anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "Remote API", "agent_type": "remote-codex", "prompt": "Implement API"},
    )

    assert runtime.pane_mgr.created_commands == [
        ["ssh", "devbox", "bash", "-lc", f"cd {tmp_path!s} && codex"]
    ]
    assert runtime.pane_mgr.sent == [(101, "Implement API")]

    _teardown_session(token)


def test_dispatch_agent_falls_back_when_agent_not_configured(monkeypatch, tmp_path):
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)
    runtime.agent_registry = built_in_agent_registry()

    anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "missing-agent", "prompt": "Implement API"},
    )

    assert runtime.plan.subtasks[0].agent_type == "claude-code"

    _teardown_session(token)


def test_run_lead_agent_records_structured_auth_startup_failure(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    memory_store = MemoryStore(base_dir=tmp_path / "memory")
    monkeypatch.setattr(lead_agent, "SessionStore", lambda: store)
    monkeypatch.setattr(lead_agent, "MemoryStore", lambda: memory_store)
    monkeypatch.setattr(
        lead_agent,
        "ClaudeSDKClient",
        lambda options: FailingClaudeClient(
            options, RuntimeError("Authentication required. Please login."), "enter"
        ),
    )

    try:
        lead_agent.run_lead_agent(
            task="Build feature",
            pane_mgr=DummyPaneManager(),
            cwd=str(tmp_path),
            session_id="startup-auth-failure",
        )
    except LeadAgentStartupError as exc:
        assert exc.category == "authentication"
        assert exc.stage == "sdk_client_startup"
        assert "openmax setup" in exc.remediation
    else:
        raise AssertionError("Expected LeadAgentStartupError")

    meta = store.load_meta("startup-auth-failure")
    events = store.load_events("startup-auth-failure")

    assert meta.status == "failed"
    assert [event.event_type for event in events][-1] == "session.startup_failed"
    assert events[-1].payload["category"] == "authentication"
    assert events[-1].payload["stage"] == "sdk_client_startup"
    assert "login" in events[-1].payload["detail"].lower()


def test_run_lead_agent_records_structured_bootstrap_failure(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    memory_store = MemoryStore(base_dir=tmp_path / "memory")
    monkeypatch.setattr(lead_agent, "SessionStore", lambda: store)
    monkeypatch.setattr(lead_agent, "MemoryStore", lambda: memory_store)
    monkeypatch.setattr(
        lead_agent,
        "ClaudeSDKClient",
        lambda options: FailingClaudeClient(
            options,
            RuntimeError("Bootstrap timed out while starting transport"),
            "query",
        ),
    )

    try:
        lead_agent.run_lead_agent(
            task="Build feature",
            pane_mgr=DummyPaneManager(),
            cwd=str(tmp_path),
            session_id="startup-bootstrap-failure",
        )
    except LeadAgentStartupError as exc:
        assert exc.category == "bootstrap"
        assert exc.stage == "prompt_submission"
    else:
        raise AssertionError("Expected LeadAgentStartupError")

    events = store.load_events("startup-bootstrap-failure")
    assert events[-1].event_type == "session.startup_failed"
    assert events[-1].payload["category"] == "bootstrap"
