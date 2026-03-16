from __future__ import annotations

import json
import subprocess
import time
from types import SimpleNamespace

import anyio

from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import LeadAgentStartupError, PlanResult, SubTask, TaskStatus
from openmax.lead_agent import core as lead_agent_core
from openmax.lead_agent import formatting as lead_agent_formatting
from openmax.lead_agent import tools as lead_agent_tools
from openmax.memory import MemoryStore
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

    def is_pane_alive(self, pane_id):
        return True

    def refresh_states(self):
        return None

    def summary(self):
        return {"total_windows": len(self.windows), "done": 0}


_fake_time = 0.0


async def _fake_run_sync(fn):
    """Async replacement for anyio.to_thread.run_sync in tests."""
    return fn()


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
    monkeypatch.setattr(lead_agent_tools.anyio, "sleep", _no_sleep)
    monkeypatch.setattr(lead_agent_tools.time, "monotonic", _fake_monotonic)


def test_dispatch_agent_persists_event(monkeypatch, tmp_path):
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "generic", "prompt": "Implement API"},
    )

    events = store.load_events("lead-test")
    assert any(event.event_type == "tool.dispatch_agent" for event in events)
    assert len(runtime.plan.subtasks) == 1
    st = runtime.plan.subtasks[0]
    assert st.name == "API"
    assert st.agent_type == "generic"
    assert st.prompt == "Implement API"
    assert st.status == TaskStatus.RUNNING
    assert st.pane_id == 101
    assert st.started_at is not None
    assert runtime.pane_mgr.sent == [(101, "Implement API")]

    _teardown_session(token)


def test_format_tool_use_humanizes_all_openmax_tools():
    _format_tool_use = lead_agent_formatting._format_tool_use
    assert (
        _format_tool_use(
            "mcp__openmax__dispatch_agent",
            {"task_name": "API routes", "agent_type": "codex"},
        )
        == "Starting agent for API routes via codex"
    )
    assert (
        _format_tool_use(
            "mcp__openmax__get_agent_recommendations",
            {"task": "Refactor API routes"},
        )
        == "Checking best agent for Refactor API routes"
    )
    assert (
        _format_tool_use(
            "mcp__openmax__read_pane_output",
            {"pane_id": 12},
        )
        == "Checking progress in pane 12"
    )
    assert (
        _format_tool_use(
            "mcp__openmax__send_text_to_pane",
            {"pane_id": 12, "text": "Please rerun the failing tests with logs"},
        )
        == "Sending follow-up to pane 12: Please rerun the failing tests with logs"
    )
    assert _format_tool_use("mcp__openmax__list_managed_panes", {}) == "Reviewing active panes"
    assert (
        _format_tool_use(
            "mcp__openmax__mark_task_done",
            {"task_name": "API routes"},
        )
        == "Marking API routes done"
    )
    assert (
        _format_tool_use(
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
        _format_tool_use(
            "mcp__openmax__remember_learning",
            {"lesson": "Prefer codex when editing Python test suites."},
        )
        == "Saving reusable lesson: Prefer codex when editing Python test suites."
    )
    assert (
        _format_tool_use(
            "mcp__openmax__report_completion",
            {"completion_pct": 100, "notes": "Everything finished and verified."},
        )
        == "Publishing completion update (100%): Everything finished and verified."
    )
    assert (
        _format_tool_use(
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
        lead_agent_tools.dispatch_agent.handler,
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
        lead_agent_tools.report_completion.handler,
        {"completion_pct": 100, "notes": "Everything finished"},
    )

    events = store.load_events("lead-test")
    assert [event.event_type for event in events][-2:] == [
        "tool.report_completion",
        "phase.anchor",
    ]
    refreshed_meta = store.load_meta(meta.session_id)
    assert refreshed_meta.latest_phase == "report"
    memories = memory_store.load_entries(str(tmp_path))
    assert memories
    assert memories[-1].kind == "run_summary"

    _teardown_session(token)


def test_remember_learning_stores_workspace_memory(tmp_path):
    _runtime, token, _store, _meta, memory_store = _setup_session(tmp_path)

    anyio.run(
        lead_agent_tools.remember_learning.handler,
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
        lead_agent_tools.get_agent_recommendations.handler,
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

    monkeypatch.setattr(lead_agent_tools.anyio, "sleep", fake_sleep)
    monkeypatch.setattr(lead_agent_tools.time, "monotonic", _fake_monotonic)
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
        lead_agent_tools.dispatch_agent.handler,
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
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "missing-agent", "prompt": "Implement API"},
    )

    assert runtime.plan.subtasks[0].agent_type == "claude-code"

    _teardown_session(token)


def test_run_lead_agent_records_structured_auth_startup_failure(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    memory_store = MemoryStore(base_dir=tmp_path / "memory")
    monkeypatch.setattr(lead_agent_core, "SessionStore", lambda: store)
    monkeypatch.setattr(lead_agent_core, "MemoryStore", lambda: memory_store)
    monkeypatch.setattr(
        lead_agent_core,
        "ClaudeSDKClient",
        lambda options: FailingClaudeClient(
            options, RuntimeError("Authentication required. Please login."), "enter"
        ),
    )

    try:
        lead_agent_core.run_lead_agent(
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
    monkeypatch.setattr(lead_agent_core, "SessionStore", lambda: store)
    monkeypatch.setattr(lead_agent_core, "MemoryStore", lambda: memory_store)
    monkeypatch.setattr(
        lead_agent_core,
        "ClaudeSDKClient",
        lambda options: FailingClaudeClient(
            options,
            RuntimeError("Bootstrap timed out while starting transport"),
            "query",
        ),
    )

    try:
        lead_agent_core.run_lead_agent(
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


def test_ask_user_returns_answer_and_persists_event(monkeypatch, tmp_path):
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    monkeypatch.setattr(
        lead_agent_tools.anyio,
        "to_thread",
        SimpleNamespace(run_sync=_fake_run_sync),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes, proceed")

    result = anyio.run(
        lead_agent_tools.ask_user.handler,
        {"question": "Should I refactor the auth module?"},
    )

    assert result["content"][0]["text"] == "yes, proceed"
    events = store.load_events("lead-test")
    ask_events = [e for e in events if e.event_type == "tool.ask_user"]
    assert len(ask_events) == 1
    assert ask_events[0].payload["question"] == "Should I refactor the auth module?"
    assert ask_events[0].payload["answer"] == "yes, proceed"

    _teardown_session(token)


def test_ask_user_pauses_and_resumes_dashboard(monkeypatch, tmp_path):
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    monkeypatch.setattr(
        lead_agent_tools.anyio,
        "to_thread",
        SimpleNamespace(run_sync=_fake_run_sync),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "ok")

    lifecycle: list[str] = []

    class FakeDashboard:
        def stop(self):
            lifecycle.append("stop")

        def start(self):
            lifecycle.append("start")

    runtime.dashboard = FakeDashboard()

    anyio.run(
        lead_agent_tools.ask_user.handler,
        {"question": "Continue?"},
    )

    assert lifecycle == ["stop", "start"]

    _teardown_session(token)


def test_format_tool_use_handles_ask_user():
    result = lead_agent_formatting._format_tool_use(
        "mcp__openmax__ask_user",
        {"question": "Which database should I target?"},
    )
    assert result == "Asking user: Which database should I target?"


def test_tool_category_and_style():
    from openmax.lead_agent.formatting import tool_category, tool_style

    assert tool_category("mcp__openmax__dispatch_agent") == "dispatch"
    assert tool_category("mcp__openmax__read_pane_output") == "monitor"
    assert tool_category("mcp__openmax__send_text_to_pane") == "intervention"
    assert tool_category("mcp__openmax__ask_user") == "intervention"
    assert tool_category("mcp__openmax__wait") == "system"
    assert tool_category("mcp__openmax__unknown_tool") == "system"

    assert tool_style("dispatch") == "bold"
    assert tool_style("monitor") == "dim"
    assert tool_style("intervention") == "bold"
    assert tool_style("system") == "dim"
    assert tool_style("unknown") == "dim"


def test_dashboard_add_tool_event():
    from openmax.dashboard import _MAX_TOOL_EVENTS, RunDashboard

    dashboard = RunDashboard("test goal")
    for i in range(_MAX_TOOL_EVENTS + 3):
        dashboard.add_tool_event(f"event {i}", "dispatch")

    assert len(dashboard.tool_events) == _MAX_TOOL_EVENTS
    assert dashboard.tool_events[0]["text"] == "event 3"
    assert dashboard.tool_events[-1]["text"] == f"event {_MAX_TOOL_EVENTS + 2}"


def test_run_verification_pass(monkeypatch, tmp_path):
    """run_verification returns pass when exit code is 0."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # Make get_text return output with exit marker
    runtime.pane_mgr.get_text = lambda pane_id: "All checks passed!\n__OPENMAX_EXIT_0__\n$ "

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "command": "ruff check src/", "timeout": 30},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["status"] == "pass"
    assert parsed["exit_code"] == 0
    assert parsed["check_type"] == "lint"
    assert "All checks passed" in parsed["output"]

    events = store.load_events("lead-test")
    verify_events = [e for e in events if e.event_type == "tool.run_verification"]
    assert len(verify_events) == 1
    assert verify_events[0].payload["status"] == "pass"

    _teardown_session(token)


def test_run_verification_fail(monkeypatch, tmp_path):
    """run_verification returns fail when exit code is non-zero."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    runtime.pane_mgr.get_text = lambda pane_id: (
        "src/foo.py:10: E501 line too long\n__OPENMAX_EXIT_1__\n$ "
    )

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "command": "ruff check src/", "timeout": 30},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["status"] == "fail"
    assert parsed["exit_code"] == 1

    _teardown_session(token)


def test_run_verification_timeout(monkeypatch, tmp_path):
    """run_verification returns timeout when no exit marker found."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # get_text never returns exit marker
    runtime.pane_mgr.get_text = lambda pane_id: "still running...\n"

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "test", "command": "pytest tests/", "timeout": 10},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["status"] == "timeout"
    assert parsed["exit_code"] is None

    _teardown_session(token)


def test_format_tool_use_run_verification():
    """_format_tool_use handles run_verification."""
    from openmax.lead_agent.formatting import _format_tool_use

    result = _format_tool_use(
        "mcp__openmax__run_verification",
        {"check_type": "lint", "command": "ruff check src/"},
    )
    assert result == "Running verification: lint"


def test_read_pane_output_returns_json_with_stuck_false(monkeypatch, tmp_path):
    """read_pane_output returns JSON with stuck=false on first read."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert "text" in parsed
    assert parsed["stuck"] is False

    _teardown_session(token)


def test_submit_plan_accepts_valid_plan(monkeypatch, tmp_path):
    """submit_plan accepts a valid plan with no cycles."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)

    result = anyio.run(
        lead_agent_tools.submit_plan.handler,
        {
            "subtasks": [
                {
                    "name": "backend",
                    "description": "Build API",
                    "files": ["src/api.py"],
                },
                {
                    "name": "frontend",
                    "description": "Build UI",
                    "files": ["src/ui.tsx"],
                    "dependencies": ["backend"],
                },
            ],
            "rationale": "Backend first, then frontend depends on API types",
            "parallel_groups": [],
        },
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["status"] == "accepted"
    assert parsed["subtask_count"] == 2
    assert runtime.plan_submitted is True

    events = store.load_events("lead-test")
    plan_events = [e for e in events if e.event_type == "tool.submit_plan"]
    assert len(plan_events) == 1

    _teardown_session(token)


def test_submit_plan_rejects_circular_dependency(monkeypatch, tmp_path):
    """submit_plan rejects a plan with circular dependencies."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    result = anyio.run(
        lead_agent_tools.submit_plan.handler,
        {
            "subtasks": [
                {"name": "A", "description": "Task A", "dependencies": ["B"]},
                {"name": "B", "description": "Task B", "dependencies": ["A"]},
            ],
            "rationale": "Circular",
            "parallel_groups": [],
        },
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert "error" in parsed
    assert "Circular" in parsed["error"] or "circular" in parsed["error"].lower()
    assert runtime.plan_submitted is not True

    _teardown_session(token)


def test_submit_plan_rejects_parallel_group_with_dependency(monkeypatch, tmp_path):
    """submit_plan rejects parallel groups where members depend on each other."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    result = anyio.run(
        lead_agent_tools.submit_plan.handler,
        {
            "subtasks": [
                {"name": "A", "description": "Task A"},
                {"name": "B", "description": "Task B", "dependencies": ["A"]},
            ],
            "rationale": "They should be parallel",
            "parallel_groups": [["A", "B"]],
        },
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert "error" in parsed
    assert "conflict" in parsed["error"].lower() or "dependency" in parsed["error"].lower()

    _teardown_session(token)


def test_dispatch_agent_warns_without_submit_plan(monkeypatch, tmp_path, capsys):
    """dispatch_agent prints a warning when called before submit_plan."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # plan_submitted is False by default
    anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "API", "agent_type": "generic", "prompt": "Implement API"},
    )

    # The warning is printed via rich console, so we check the subtask was still created
    assert len(runtime.plan.subtasks) == 1
    assert runtime.plan_submitted is False

    _teardown_session(token)


def test_format_tool_use_submit_plan():
    """_format_tool_use handles submit_plan."""
    from openmax.lead_agent.formatting import _format_tool_use

    result = _format_tool_use(
        "mcp__openmax__submit_plan",
        {"subtasks": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
    )
    assert result == "Submitting plan with 3 subtasks"


def test_read_pane_output_detects_stuck_after_three_identical_reads(monkeypatch, tmp_path):
    """Three consecutive identical outputs trigger stuck=true."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    # DummyPaneManager.get_text returns the same text for same pane_id,
    # so 3 reads should trigger stuck detection.
    for _ in range(2):
        result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": 101},
        )
        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["stuck"] is False

    # Third read — should be stuck
    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )
    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["stuck"] is True

    _teardown_session(token)


def test_read_pane_output_resets_stuck_on_new_output(monkeypatch, tmp_path):
    """Stuck resets when output changes."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    call_count = 0

    def varying_get_text(pane_id):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return "same output\n$ "
        return f"new output {call_count}\n$ "

    runtime.pane_mgr.get_text = varying_get_text

    # 3 identical reads → stuck
    for _ in range(3):
        result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": 101},
        )
    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["stuck"] is True

    # New output → not stuck
    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )
    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["stuck"] is False

    _teardown_session(token)


def test_read_pane_output_returns_exited_when_pane_dead(monkeypatch, tmp_path):
    """read_pane_output returns exited=true when the pane is no longer alive."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    class DeadPaneManager:
        def get_text(self, pane_id):
            return "output before death"

        def is_pane_alive(self, pane_id):
            return False

    runtime.pane_mgr = DeadPaneManager()
    runtime.pane_output_hashes = {}

    monkeypatch.setattr(lead_agent_tools.anyio, "sleep", _no_sleep)

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 123},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["exited"] is True
    # Early exit returns placeholder since pane is already gone
    assert parsed["stuck"] is False

    _teardown_session(token)


def test_read_pane_output_returns_exited_false_when_pane_alive(monkeypatch, tmp_path):
    """read_pane_output returns exited=false when pane is alive."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    class AlivePaneManager:
        def get_text(self, pane_id):
            return "still running"

        def is_pane_alive(self, pane_id):
            return True

    runtime.pane_mgr = AlivePaneManager()
    runtime.pane_output_hashes = {}

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 123},
    )

    import json as _json

    parsed = _json.loads(result["content"][0]["text"])
    assert parsed["exited"] is False

    _teardown_session(token)


def test_transition_phase_valid(tmp_path):
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    runtime.current_phase = "research"

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "research",
                "to_phase": "implement",
                "gate_summary": "Plan complete with 3 subtasks identified",
                "artifacts": ["plan.md"],
            },
        )
        text = result["content"][0]["text"]
        assert "Transitioned" in text
        assert runtime.current_phase == "implement"
        assert runtime.session_meta.latest_phase == "implement"
    finally:
        _teardown_session(token)


def test_transition_phase_short_summary_rejected(tmp_path):
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "research",
                "to_phase": "implement",
                "gate_summary": "short",
                "artifacts": [],
            },
        )
        text = result["content"][0]["text"]
        assert "Error" in text
        assert "20 characters" in text
    finally:
        _teardown_session(token)


def test_transition_phase_mismatched_phase_rejected(tmp_path):
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    runtime.current_phase = "research"

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "implement",
                "to_phase": "verify",
                "gate_summary": "This should fail because phase mismatch",
                "artifacts": [],
            },
        )
        text = result["content"][0]["text"]
        assert "Error" in text
        assert "does not match" in text
    finally:
        _teardown_session(token)


def test_transition_phase_invalid_to_phase_rejected(tmp_path):
    """Cannot skip phases — research must go to implement, not verify."""
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    runtime.current_phase = "research"

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "research",
                "to_phase": "verify",
                "gate_summary": "Trying to skip directly to verify phase",
                "artifacts": [],
            },
        )
        text = result["content"][0]["text"]
        assert "Error" in text
        assert "invalid transition" in text
        assert runtime.current_phase == "research"  # unchanged
    finally:
        _teardown_session(token)


def test_transition_phase_verify_to_implement_redispatch(tmp_path):
    """Allow verify → implement for re-dispatch scenarios."""
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    runtime.current_phase = "verify"

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "verify",
                "to_phase": "implement",
                "gate_summary": "Tests failed, need to re-dispatch agent to fix",
                "artifacts": [],
            },
        )
        text = result["content"][0]["text"]
        assert "Transitioned" in text
        assert runtime.current_phase == "implement"
    finally:
        _teardown_session(token)


def test_dispatch_agent_injects_memory_context(monkeypatch, tmp_path):
    """dispatch_agent appends workspace memory context to the prompt."""
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # Record a lesson so build_context returns something
    memory_store.record_lesson(
        cwd=str(tmp_path),
        task="test task",
        lesson="Always use pytest for testing",
        rationale="Standard practice",
    )

    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "test-task",
                "agent_type": "claude-code",
                "prompt": "Do something",
            },
        )

        # The prompt sent to the pane should include memory context
        assert len(runtime.pane_mgr.sent) > 0
        sent_text = runtime.pane_mgr.sent[0][1]
        assert "Workspace Context" in sent_text
        assert "pytest" in sent_text
    finally:
        _teardown_session(token)


def test_mark_task_done_stores_completion_notes(tmp_path):
    """mark_task_done stores completion_notes on the subtask."""
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)

    subtask = SubTask(
        name="test-task",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        anyio.run(
            lead_agent_tools.mark_task_done.handler,
            {
                "task_name": "test-task",
                "notes": "Completed with 5 tests passing",
            },
        )

        assert runtime.plan.subtasks[0].completion_notes == ("Completed with 5 tests passing")
        assert runtime.plan.subtasks[0].status == TaskStatus.DONE
    finally:
        _teardown_session(token)


def test_mark_task_done_no_notes(tmp_path):
    """mark_task_done works without notes (backwards compat)."""
    runtime, token, store, meta, memory_store = _setup_session(tmp_path)

    subtask = SubTask(
        name="test-task",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        anyio.run(
            lead_agent_tools.mark_task_done.handler,
            {"task_name": "test-task"},
        )

        assert runtime.plan.subtasks[0].completion_notes is None
        assert runtime.plan.subtasks[0].status == TaskStatus.DONE
    finally:
        _teardown_session(token)


def test_read_pane_output_stuck_event_recorded(monkeypatch, tmp_path):
    """Session event records stuck=true when detected."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)

    for _ in range(3):
        anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": 101},
        )

    events = store.load_events("lead-test")
    read_events = [e for e in events if e.event_type == "tool.read_pane_output"]
    assert len(read_events) == 3
    assert read_events[0].payload["stuck"] is False
    assert read_events[1].payload["stuck"] is False
    assert read_events[2].payload["stuck"] is True

    _teardown_session(token)


def test_dispatch_agent_event_contains_recommended_agent(monkeypatch, tmp_path):
    """dispatch_agent event payload includes recommended_agent from memory rankings."""
    runtime, token, store, _meta, memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # Record agent completions so derive_agent_rankings returns results
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
        anchors=[{"summary": "API work succeeded"}],
    )

    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "Refactor API",
                "agent_type": "codex",
                "prompt": "Refactor API endpoints",
            },
        )

        events = store.load_events("lead-test")
        dispatch_events = [e for e in events if e.event_type == "tool.dispatch_agent"]
        assert len(dispatch_events) == 1
        payload = dispatch_events[0].payload
        assert "recommended_agent" in payload
        assert payload["recommended_agent"] is not None
    finally:
        _teardown_session(token)


def test_dispatch_agent_event_contains_override_reason(monkeypatch, tmp_path):
    """dispatch_agent event payload includes override_reason when provided."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "API",
                "agent_type": "claude-code",
                "prompt": "Implement API",
                "override_reason": "User prefers claude-code for this task",
            },
        )

        events = store.load_events("lead-test")
        dispatch_events = [e for e in events if e.event_type == "tool.dispatch_agent"]
        assert len(dispatch_events) == 1
        payload = dispatch_events[0].payload
        assert payload["override_reason"] == "User prefers claude-code for this task"
    finally:
        _teardown_session(token)


def test_dispatch_agent_sets_started_at(monkeypatch, tmp_path):
    """dispatch_agent sets started_at on the subtask."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        before = time.time()
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "test-task",
                "agent_type": "claude-code",
                "prompt": "Test",
            },
        )
        after = time.time()

        subtask = runtime.plan.subtasks[0]
        assert subtask.started_at is not None
        assert before <= subtask.started_at <= after
    finally:
        _teardown_session(token)


def test_check_conflicts_clean_state(monkeypatch, tmp_path):
    """check_conflicts returns no conflicts for a clean repo."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)

    import subprocess as _subprocess

    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        result = _subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "status" in cmd:
            result.stdout = "?? untracked.txt\n"
        return result

    monkeypatch.setattr(lead_agent_tools.subprocess, "run", mock_run)

    try:
        result = anyio.run(
            lead_agent_tools.check_conflicts.handler,
            {},
        )

        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["conflict"] is False
        assert parsed["details"] == "No conflicts detected"
        assert parsed["untracked_files"] == ["untracked.txt"]

        events = store.load_events("lead-test")
        conflict_events = [e for e in events if e.event_type == "tool.check_conflicts"]
        assert len(conflict_events) == 1
    finally:
        _teardown_session(token)


def test_check_conflicts_with_conflicts(monkeypatch, tmp_path):
    """check_conflicts detects conflict markers."""
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)

    import subprocess as _subprocess

    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if "diff" in cmd:
            return _subprocess.CompletedProcess(
                cmd, 1, stdout="src/foo.py:10: leftover conflict marker\n", stderr=""
            )
        return _subprocess.CompletedProcess(cmd, 0, stdout="UU src/foo.py\n?? new.txt\n", stderr="")

    monkeypatch.setattr(lead_agent_tools.subprocess, "run", mock_run)

    try:
        result = anyio.run(
            lead_agent_tools.check_conflicts.handler,
            {},
        )

        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["conflict"] is True
        assert "conflict" in parsed["details"].lower() or "leftover" in parsed["details"].lower()
        assert "new.txt" in parsed["untracked_files"]
    finally:
        _teardown_session(token)


def test_format_tool_use_check_conflicts():
    """_format_tool_use handles check_conflicts."""
    from openmax.lead_agent.formatting import _format_tool_use

    result = _format_tool_use("mcp__openmax__check_conflicts", {})
    assert result == "Checking for git conflicts"


def test_mark_task_done_sets_finished_at(tmp_path):
    """mark_task_done sets finished_at on the subtask."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    subtask = SubTask(
        name="test-task",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
        started_at=time.time() - 60,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        before = time.time()
        anyio.run(
            lead_agent_tools.mark_task_done.handler,
            {"task_name": "test-task"},
        )
        after = time.time()

        assert subtask.finished_at is not None
        assert before <= subtask.finished_at <= after
    finally:
        _teardown_session(token)


def test_read_pane_output_includes_retry_info_on_exit(monkeypatch, tmp_path):
    """When pane has exited, read_pane_output includes retry_count and can_retry."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    class DeadPaneManager:
        def get_text(self, pane_id):
            return "Traceback: error occurred"

        def is_pane_alive(self, pane_id):
            return False

    runtime.pane_mgr = DeadPaneManager()
    runtime.pane_output_hashes = {}

    # Register a subtask with pane_id=123 and retry_count=0
    subtask = SubTask(
        name="failing-task",
        agent_type="claude-code",
        prompt="do stuff",
        status=TaskStatus.RUNNING,
        pane_id=123,
        retry_count=0,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": 123},
        )
        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["exited"] is True
        assert parsed["retry_count"] == 0
        assert parsed["max_retries"] == 2
        assert parsed["can_retry"] is True
        assert parsed["task_name"] == "failing-task"
    finally:
        _teardown_session(token)


def test_read_pane_output_can_retry_false_at_max(monkeypatch, tmp_path):
    """When retry_count >= max_retries, can_retry is False."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    class DeadPaneManager:
        def get_text(self, pane_id):
            return "fatal error"

        def is_pane_alive(self, pane_id):
            return False

    runtime.pane_mgr = DeadPaneManager()
    runtime.pane_output_hashes = {}

    subtask = SubTask(
        name="exhausted-task",
        agent_type="claude-code",
        prompt="do stuff",
        status=TaskStatus.RUNNING,
        pane_id=456,
        retry_count=2,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        result = anyio.run(
            lead_agent_tools.read_pane_output.handler,
            {"pane_id": 456},
        )
        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["exited"] is True
        assert parsed["retry_count"] == 2
        assert parsed["can_retry"] is False
    finally:
        _teardown_session(token)


def test_dispatch_agent_carries_retry_count(monkeypatch, tmp_path):
    """dispatch_agent with retry_count>0 updates the existing subtask."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # Pre-populate a failed subtask
    subtask = SubTask(
        name="retry-task",
        agent_type="claude-code",
        prompt="original prompt",
        status=TaskStatus.ERROR,
        pane_id=99,
        retry_count=0,
    )
    runtime.plan.subtasks.append(subtask)

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "retry-task",
                "agent_type": "claude-code",
                "prompt": "retry with fix",
                "retry_count": 1,
            },
        )
        import json as _json

        parsed = _json.loads(result["content"][0]["text"])
        assert parsed["status"] == "dispatched"
        assert parsed["retry_count"] == 1

        # The subtask should be updated in-place with new retry_count
        matching = [st for st in runtime.plan.subtasks if st.name == "retry-task"]
        assert len(matching) == 1
        assert matching[0].retry_count == 1
        assert matching[0].status == TaskStatus.RUNNING
    finally:
        _teardown_session(token)


def test_subtask_max_retries_default():
    """SubTask.max_retries defaults to 2."""
    st = SubTask(name="t", agent_type="claude-code", prompt="p")
    assert st.max_retries == 2
    assert st.retry_count == 0


# ── Branch Isolation Tests ──────────────────────────────────────────────


def _init_git_repo(path):
    """Create a minimal git repo at path with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        capture_output=True,
    )
    # Create initial commit
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )


def test_dispatch_creates_branch_and_worktree(monkeypatch, tmp_path):
    """dispatch_agent creates an isolated branch + worktree in a git repo."""
    _init_git_repo(tmp_path)
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "my-feature", "agent_type": "generic", "prompt": "Do stuff"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "dispatched"
        assert parsed["branch_name"] == "openmax/my-feature"

        # SubTask should have branch_name set
        st = runtime.plan.subtasks[0]
        assert st.branch_name == "openmax/my-feature"

        # Worktree should exist
        worktree_dir = tmp_path / ".openmax-worktrees" / "openmax_my-feature"
        assert worktree_dir.exists()

        # Branch should exist
        branches = subprocess.run(
            ["git", "branch", "--list", "openmax/my-feature"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert "openmax/my-feature" in branches.stdout
    finally:
        _teardown_session(token)


def test_dispatch_fallback_when_no_git(monkeypatch, tmp_path):
    """dispatch_agent gracefully falls back when not in a git repo."""
    # tmp_path is not a git repo, so branch creation should fail gracefully
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "no-git", "agent_type": "generic", "prompt": "Do stuff"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "dispatched"
        # branch_name should be None since git failed
        assert parsed["branch_name"] is None

        st = runtime.plan.subtasks[0]
        assert st.branch_name is None
    finally:
        _teardown_session(token)


def test_merge_agent_branch_success(monkeypatch, tmp_path):
    """merge_agent_branch merges a branch with non-conflicting changes."""
    _init_git_repo(tmp_path)
    runtime, token, store, _meta, _memory_store = _setup_session(tmp_path)
    runtime.integration_branch = "main"
    _patch_time(monkeypatch)

    try:
        # Dispatch to create branch + worktree
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "feat-a", "agent_type": "generic", "prompt": "Add file"},
        )
        st = runtime.plan.subtasks[0]
        assert st.branch_name == "openmax/feat-a"

        # Simulate agent making a change in the worktree
        worktree_dir = tmp_path / ".openmax-worktrees" / "openmax_feat-a"
        (worktree_dir / "new_file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=str(worktree_dir), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add new_file"],
            cwd=str(worktree_dir),
            capture_output=True,
            check=True,
        )

        # Mark done and merge
        st.status = TaskStatus.DONE
        result = anyio.run(
            lead_agent_tools.merge_agent_branch.handler,
            {"task_name": "feat-a"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "merged"
        assert len(parsed["commit"]) >= 7

        # File should exist on main
        assert (tmp_path / "new_file.txt").exists()

        # Session event should be recorded
        events = store.load_events("lead-test")
        merge_events = [e for e in events if e.event_type == "tool.merge_agent_branch"]
        assert len(merge_events) == 1
        assert merge_events[0].payload["status"] == "merged"
    finally:
        _teardown_session(token)


def test_merge_agent_branch_conflict(monkeypatch, tmp_path):
    """merge_agent_branch detects and reports conflicts."""
    _init_git_repo(tmp_path)
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    runtime.integration_branch = "main"
    _patch_time(monkeypatch)

    try:
        # Dispatch to create branch
        anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "feat-conflict", "agent_type": "generic", "prompt": "Edit README"},
        )
        st = runtime.plan.subtasks[0]

        # Simulate agent editing README in worktree
        worktree_dir = tmp_path / ".openmax-worktrees" / "openmax_feat-conflict"
        (worktree_dir / "README.md").write_text("# agent version\n")
        subprocess.run(["git", "add", "."], cwd=str(worktree_dir), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "agent edit"],
            cwd=str(worktree_dir),
            capture_output=True,
            check=True,
        )

        # Also edit README on main (creating conflict)
        (tmp_path / "README.md").write_text("# main version\n")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "main edit"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )

        # Attempt merge — should conflict
        st.status = TaskStatus.DONE
        result = anyio.run(
            lead_agent_tools.merge_agent_branch.handler,
            {"task_name": "feat-conflict"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "conflict"
        assert isinstance(parsed["files"], list)
    finally:
        _teardown_session(token)


def test_merge_agent_branch_no_branch(monkeypatch, tmp_path):
    """merge_agent_branch returns skipped when task has no branch."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    # Add a subtask without branch_name
    st = SubTask(name="no-branch", agent_type="generic", prompt="p", status=TaskStatus.DONE)
    runtime.plan.subtasks.append(st)

    try:
        result = anyio.run(
            lead_agent_tools.merge_agent_branch.handler,
            {"task_name": "no-branch"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "skipped"
    finally:
        _teardown_session(token)


def test_merge_agent_branch_task_not_found(monkeypatch, tmp_path):
    """merge_agent_branch returns error for unknown task name."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        result = anyio.run(
            lead_agent_tools.merge_agent_branch.handler,
            {"task_name": "nonexistent"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed
    finally:
        _teardown_session(token)


def test_submit_plan_file_overlap_warning(monkeypatch, tmp_path):
    """submit_plan warns about file overlaps in parallel groups."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    try:
        result = anyio.run(
            lead_agent_tools.submit_plan.handler,
            {
                "subtasks": [
                    {
                        "name": "a",
                        "description": "Task A",
                        "files": ["src/api.py", "src/shared.py"],
                        "dependencies": [],
                    },
                    {
                        "name": "b",
                        "description": "Task B",
                        "files": ["src/db.py", "src/shared.py"],
                        "dependencies": [],
                    },
                ],
                "rationale": "Two parallel tasks with shared file",
                "parallel_groups": [["a", "b"]],
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "accepted"
        assert "file_overlap_warnings" in parsed
        assert len(parsed["file_overlap_warnings"]) == 1
        assert "src/shared.py" in parsed["file_overlap_warnings"][0]
    finally:
        _teardown_session(token)


def test_submit_plan_no_file_overlap(monkeypatch, tmp_path):
    """submit_plan does not warn when parallel group files are disjoint."""
    runtime, token, _store, _meta, _memory_store = _setup_session(tmp_path)

    try:
        result = anyio.run(
            lead_agent_tools.submit_plan.handler,
            {
                "subtasks": [
                    {
                        "name": "a",
                        "description": "Task A",
                        "files": ["src/api.py"],
                        "dependencies": [],
                    },
                    {
                        "name": "b",
                        "description": "Task B",
                        "files": ["src/db.py"],
                        "dependencies": [],
                    },
                ],
                "rationale": "Non-overlapping tasks",
                "parallel_groups": [["a", "b"]],
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "accepted"
        assert "file_overlap_warnings" not in parsed
    finally:
        _teardown_session(token)


def test_format_tool_use_merge_agent_branch():
    """_format_tool_use formats merge_agent_branch correctly."""
    result = lead_agent_formatting._format_tool_use(
        "mcp__openmax__merge_agent_branch",
        {"task_name": "my-feature"},
    )
    assert "my-feature" in result
    assert "Merging" in result


def test_sanitize_branch_name():
    """_sanitize_branch_name produces valid git branch names."""
    assert lead_agent_tools._sanitize_branch_name("my feature") == "openmax/my-feature"
    assert lead_agent_tools._sanitize_branch_name("a/b/c") == "openmax/a-b-c"
    assert lead_agent_tools._sanitize_branch_name("ok-name") == "openmax/ok-name"


def test_session_runtime_merge_event_reconstruction(tmp_path):
    """reconstruct_plan handles tool.merge_agent_branch events."""
    from openmax.session_runtime import ContextBuilder, SessionMeta

    meta = SessionMeta(
        session_id="test",
        task="test goal",
        cwd=str(tmp_path),
        task_hash="abc",
        status="active",
    )
    from openmax.session_runtime import LeadEvent

    events = [
        LeadEvent(
            event_id="e1",
            event_type="tool.merge_agent_branch",
            session_id="test",
            cwd=str(tmp_path),
            task_hash="abc",
            timestamp="2026-01-01T00:00:00+00:00",
            payload={
                "status": "merged",
                "task_name": "feat-a",
                "commit": "abc1234567890",
            },
        ),
    ]

    builder = ContextBuilder()
    plan = builder.reconstruct_plan(meta, events)
    assert any("Merged branch for 'feat-a'" in a for a in plan.recent_activity)
