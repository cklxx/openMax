from __future__ import annotations

from types import SimpleNamespace

import anyio

from openmax import lead_agent
from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import PlanResult, SubTask, TaskStatus
from openmax.memory_system import MemoryStore
from openmax.session_runtime import SessionStore


class DummyPaneManager:
    def __init__(self) -> None:
        self.windows: dict[int, SimpleNamespace] = {}
        self.sent: list[tuple[int, str]] = []
        self.created_commands: list[list[str]] = []

    def create_window(self, command, purpose, agent_type, title, cwd):
        self.created_commands.append(command)
        self.windows[7] = SimpleNamespace(pane_ids=[101])
        return SimpleNamespace(pane_id=101, window_id=7)

    def add_pane(self, window_id, command, purpose, agent_type, cwd):
        self.created_commands.append(command)
        self.windows[window_id].pane_ids.append(102)
        return SimpleNamespace(pane_id=102, window_id=window_id)

    def send_text(self, pane_id, text):
        self.sent.append((pane_id, text))

    def get_text(self, pane_id):
        return f"pane {pane_id} output"

    def refresh_states(self):
        return None

    def summary(self):
        return {"total_windows": len(self.windows), "done": 0}


async def _no_sleep(_seconds: float) -> None:
    return None


def _setup_session(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("lead-test", "Goal", str(tmp_path))
    memory_store = MemoryStore(base_dir=tmp_path / "memory")
    lead_agent._session_store = store
    lead_agent._session_meta = meta
    lead_agent._memory_store = memory_store
    lead_agent._plan = PlanResult(goal="Goal")
    lead_agent._cwd = str(tmp_path)
    lead_agent._agent_window_id = None
    lead_agent._pane_mgr = DummyPaneManager()
    lead_agent._agent_registry = built_in_agent_registry()
    return store, meta, memory_store


def _teardown_session():
    lead_agent._session_store = None
    lead_agent._session_meta = None
    lead_agent._memory_store = None
    lead_agent._plan = None
    lead_agent._pane_mgr = None
    lead_agent._agent_window_id = None
    lead_agent._agent_registry = built_in_agent_registry()


def test_dispatch_agent_persists_event(monkeypatch, tmp_path):
    store, _meta, _memory_store = _setup_session(tmp_path)
    monkeypatch.setattr(lead_agent.anyio, "sleep", _no_sleep)

    anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "generic", "prompt": "Implement API"},
    )

    events = store.load_events("lead-test")
    assert any(event.event_type == "tool.dispatch_agent" for event in events)
    assert lead_agent._plan.subtasks == [
        SubTask(
            name="API",
            agent_type="generic",
            prompt="Implement API",
            status=TaskStatus.RUNNING,
            pane_id=101,
        )
    ]
    assert lead_agent._pane_mgr.sent == [(101, "Implement API")]

    _teardown_session()


def test_dispatch_agent_enforces_allowed_agents(monkeypatch, tmp_path):
    _store, _meta, _memory_store = _setup_session(tmp_path)
    monkeypatch.setattr(lead_agent.anyio, "sleep", _no_sleep)
    lead_agent._allowed_agents = ["codex"]

    result = anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "claude-code", "prompt": "Implement API"},
    )

    # Should have fallen back to codex (first in allowed list)
    subtask = lead_agent._plan.subtasks[0]
    assert subtask.agent_type == "codex"
    import json

    dispatched = json.loads(result["content"][0]["text"])
    assert dispatched["agent_type"] == "codex"

    lead_agent._allowed_agents = None
    _teardown_session()


def test_report_completion_writes_report_and_anchor(tmp_path):
    store, meta, memory_store = _setup_session(tmp_path)
    lead_agent._plan.subtasks.append(
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

    _teardown_session()


def test_remember_learning_stores_workspace_memory(tmp_path):
    _store, _meta, memory_store = _setup_session(tmp_path)

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

    _teardown_session()


def test_get_agent_recommendations_returns_ranked_json(tmp_path):
    _store, _meta, memory_store = _setup_session(tmp_path)
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

    _teardown_session()


def test_dispatch_agent_uses_configured_custom_agent(monkeypatch, tmp_path):
    _store, _meta, _memory_store = _setup_session(tmp_path)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(lead_agent.anyio, "sleep", fake_sleep)
    lead_agent._agent_registry = built_in_agent_registry().with_definition(
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

    assert lead_agent._pane_mgr.created_commands == [
        ["ssh", "devbox", "bash", "-lc", f"cd {tmp_path!s} && codex"]
    ]
    assert sleep_calls == [9]
    assert lead_agent._pane_mgr.sent == [(101, "Implement API")]

    _teardown_session()


def test_dispatch_agent_falls_back_when_agent_not_configured(monkeypatch, tmp_path):
    _store, _meta, _memory_store = _setup_session(tmp_path)
    monkeypatch.setattr(lead_agent.anyio, "sleep", _no_sleep)
    lead_agent._agent_registry = built_in_agent_registry()

    anyio.run(
        lead_agent.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "missing-agent", "prompt": "Implement API"},
    )

    assert lead_agent._plan.subtasks[0].agent_type == "claude-code"

    _teardown_session()
