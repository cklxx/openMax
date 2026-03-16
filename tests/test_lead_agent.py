from __future__ import annotations

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

    assert tool_style("dispatch") == "bold green"
    assert tool_style("monitor") == "cyan"
    assert tool_style("intervention") == "bold yellow"
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
    assert "output before death" in parsed["text"]

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
    runtime.session_meta.latest_phase = "research"
    store.save_meta(runtime.session_meta)

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
    runtime.session_meta.latest_phase = "research"
    store.save_meta(runtime.session_meta)

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
