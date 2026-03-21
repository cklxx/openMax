from __future__ import annotations

import json
import subprocess
import time
from types import SimpleNamespace

import anyio

import tests.conftest as _conftest
from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import LeadAgentStartupError, PlanResult, SubTask, TaskStatus
from openmax.lead_agent import core as lead_agent_core
from openmax.lead_agent import formatting as lead_agent_formatting
from openmax.lead_agent import tools as lead_agent_tools
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.session_runtime import SessionStore
from tests.conftest import _fake_monotonic, _no_sleep
from tests.conftest import patch_time as _patch_time


class DummyPaneManager:
    def __init__(self) -> None:
        self.windows: dict[int, SimpleNamespace] = {}
        self.sent: list[tuple[int, str]] = []
        self.created_commands: list[list[str]] = []

    def create_window(self, command, purpose, agent_type, title, cwd, env=None):
        self.created_commands.append(command)
        self.windows[7] = SimpleNamespace(pane_ids=[101])
        return SimpleNamespace(pane_id=101, window_id=7)

    def add_pane(self, window_id, command, purpose, agent_type, cwd, env=None, title=None):
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


async def _fake_run_sync(fn):
    """Async replacement for anyio.to_thread.run_sync in tests."""
    return fn()


def _setup_session(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("lead-test", "Goal", str(tmp_path))
    runtime = LeadAgentRuntime(
        cwd=str(tmp_path),
        plan=PlanResult(goal="Goal"),
        pane_mgr=DummyPaneManager(),
        session_store=store,
        session_meta=meta,
        agent_registry=built_in_agent_registry(),
        plan_confirm=False,
    )
    token = bind_lead_agent_runtime(runtime)
    return runtime, token, store, meta


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


def test_dispatch_agent_persists_event(monkeypatch, tmp_path):
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    assert st.prompt.startswith("Implement API")
    assert "## File Protocol (openMax)" in st.prompt
    assert 'session_id="lead-test"' in st.prompt
    assert st.status == TaskStatus.RUNNING
    assert st.pane_id == 101
    assert st.started_at is not None
    assert len(runtime.pane_mgr.sent) == 1
    assert runtime.pane_mgr.sent[0][0] == 101
    assert runtime.pane_mgr.sent[0][1].startswith("Implement API")

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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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


def test_agent_strategy_hint_both():
    from openmax.lead_agent.core import _agent_strategy_hint

    hint = _agent_strategy_hint(["claude-code", "codex"])
    assert "auto-inferred" in hint
    assert "role" in hint


def test_agent_strategy_hint_single():
    from openmax.lead_agent.core import _agent_strategy_hint

    assert "Prefer 'codex'" in _agent_strategy_hint(["codex"])
    assert "Prefer 'claude-code'" in _agent_strategy_hint(["claude-code"])


def test_report_completion_writes_report_and_anchor(tmp_path):
    runtime, token, store, meta = _setup_session(tmp_path)
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

    _teardown_session(token)


def test_dispatch_agent_uses_configured_custom_agent(monkeypatch, tmp_path):
    runtime, token, _store, _meta = _setup_session(tmp_path)
    sleep_calls: list[float] = []
    _conftest._fake_time = 0.0

    async def fake_sleep(seconds: float) -> None:
        _conftest._fake_time += seconds
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
    assert len(runtime.pane_mgr.sent) == 1
    assert runtime.pane_mgr.sent[0][0] == 101
    assert runtime.pane_mgr.sent[0][1].startswith("Implement API")

    _teardown_session(token)


def test_dispatch_agent_falls_back_when_agent_not_configured(monkeypatch, tmp_path):
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    monkeypatch.setattr(lead_agent_core, "SessionStore", lambda: store)
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
    monkeypatch.setattr(lead_agent_core, "SessionStore", lambda: store)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, store, meta = _setup_session(tmp_path)
    runtime.current_phase = "research"

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "research",
                "to_phase": "plan",
                "gate_summary": "Research complete, relevant files and data flow identified",
                "artifacts": ["research-report.md"],
            },
        )
        text = result["content"][0]["text"]
        assert "Transitioned" in text
        assert runtime.current_phase == "plan"
        assert runtime.session_meta.latest_phase == "plan"

        # follow-on: plan → implement
        result2 = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "plan",
                "to_phase": "implement",
                "gate_summary": "Plan submitted with 3 subtasks in 2 parallel groups",
                "artifacts": ["plan.md"],
            },
        )
        text2 = result2["content"][0]["text"]
        assert "Transitioned" in text2
        assert runtime.current_phase == "implement"
    finally:
        _teardown_session(token)


def test_transition_phase_short_summary_rejected(tmp_path):
    runtime, token, store, meta = _setup_session(tmp_path)

    try:
        result = anyio.run(
            lead_agent_tools.transition_phase.handler,
            {
                "from_phase": "research",
                "to_phase": "plan",
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
    runtime, token, store, meta = _setup_session(tmp_path)
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
    runtime, token, store, meta = _setup_session(tmp_path)
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
    runtime, token, store, meta = _setup_session(tmp_path)
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


def test_compress_context_under_budget_passthrough():
    """_compress_context returns text unchanged when under budget."""
    short_text = "This is a short context."
    result = lead_agent_tools._compress_context(short_text, budget=2000)
    assert result == short_text


def test_compress_context_over_budget_truncates():
    """_compress_context compresses long text to fit within budget."""
    # Build a long context: first paragraph + many bullet lines
    lines = ["First paragraph with important info.", ""]
    for i in range(200):
        lines.append(f"- Bullet point number {i} with some filler text to pad length")
    long_text = "\n".join(lines)

    budget = 100  # ~400 chars
    result = lead_agent_tools._compress_context(long_text, budget)

    # Result should be significantly shorter than original
    assert len(result) <= budget * 4
    # First paragraph should be preserved
    assert "First paragraph" in result
    # Should contain some bullets but not all
    assert "- Bullet point" in result
    assert len(result) < len(long_text)


def test_mark_task_done_stores_completion_notes(tmp_path):
    """mark_task_done stores completion_notes on the subtask."""
    runtime, token, store, meta = _setup_session(tmp_path)

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
    runtime, token, store, meta = _setup_session(tmp_path)

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
    runtime, token, store, _meta = _setup_session(tmp_path)

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


def test_dispatch_agent_event_contains_override_reason(monkeypatch, tmp_path):
    """dispatch_agent event payload includes override_reason when provided."""
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)

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
    runtime, token, store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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

        # Attempt merge — trivial conflict is auto-resolved
        st.status = TaskStatus.DONE
        result = anyio.run(
            lead_agent_tools.merge_agent_branch.handler,
            {"task_name": "feat-conflict"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "merged"
    finally:
        _teardown_session(token)


def test_merge_agent_branch_no_branch(monkeypatch, tmp_path):
    """merge_agent_branch returns skipped when task has no branch."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)
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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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
    runtime, token, _store, _meta = _setup_session(tmp_path)

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


# ── Cost Convergence & Budget Control Tests ─────────────────────────────


def test_check_budget_warning_soft_limit():
    """_check_budget_warning returns soft_limit at 80% usage."""
    from openmax.lead_agent.tools import _check_budget_warning

    assert _check_budget_warning("task-a", 800, 1000) == "soft_limit"
    assert _check_budget_warning("task-a", 900, 1000) == "soft_limit"
    assert _check_budget_warning("task-a", 799, 1000) is None
    assert _check_budget_warning("task-a", 0, 1000) is None


def test_check_budget_warning_hard_limit():
    """_check_budget_warning returns hard_limit at 100% usage."""
    from openmax.lead_agent.tools import _check_budget_warning

    assert _check_budget_warning("task-a", 1000, 1000) == "hard_limit"
    assert _check_budget_warning("task-a", 1500, 1000) == "hard_limit"


def test_dispatch_agent_accepts_token_budget(monkeypatch, tmp_path):
    """dispatch_agent accepts token_budget and stores it on the subtask."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "budgeted-task",
                "agent_type": "claude-code",
                "prompt": "Do something",
                "token_budget": 5000,
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "dispatched"
        assert parsed["token_budget"] == 5000

        st = runtime.plan.subtasks[0]
        assert st.token_budget == 5000
        assert st.tokens_used == 0
    finally:
        _teardown_session(token)


def test_dispatch_agent_no_budget_defaults_none(monkeypatch, tmp_path):
    """dispatch_agent without token_budget leaves it as None."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "no-budget-task",
                "agent_type": "claude-code",
                "prompt": "Do something",
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["token_budget"] is None

        st = runtime.plan.subtasks[0]
        assert st.token_budget is None
    finally:
        _teardown_session(token)


def test_read_pane_output_includes_budget_status(monkeypatch, tmp_path):
    """read_pane_output includes budget info when subtask has a token_budget."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    subtask = SubTask(
        name="budgeted",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
        token_budget=10000,
        tokens_used=8500,
    )
    runtime.plan.subtasks.append(subtask)

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert "budget" in parsed
    assert parsed["budget"]["token_budget"] == 10000
    assert parsed["budget"]["tokens_used"] == 8500
    assert parsed["budget"]["warning"] == "soft_limit"

    _teardown_session(token)


def test_read_pane_output_budget_hard_limit(monkeypatch, tmp_path):
    """read_pane_output shows hard_limit when tokens_used >= token_budget."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    subtask = SubTask(
        name="over-budget",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
        token_budget=5000,
        tokens_used=5000,
    )
    runtime.plan.subtasks.append(subtask)

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert parsed["budget"]["warning"] == "hard_limit"

    _teardown_session(token)


def test_read_pane_output_no_budget_no_field(monkeypatch, tmp_path):
    """read_pane_output omits budget field when no token_budget is set."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    subtask = SubTask(
        name="unbounded",
        agent_type="claude-code",
        prompt="test",
        status=TaskStatus.RUNNING,
        pane_id=101,
    )
    runtime.plan.subtasks.append(subtask)

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert "budget" not in parsed

    _teardown_session(token)


def test_subtask_token_budget_defaults():
    """SubTask token_budget defaults to None and tokens_used to 0."""
    st = SubTask(name="t", agent_type="claude-code", prompt="p")
    assert st.token_budget is None
    assert st.tokens_used == 0


def test_plan_submission_total_budget():
    """PlanSubmission supports total_budget field."""
    from openmax.lead_agent.types import PlanSubmission

    plan = PlanSubmission(rationale="test", total_budget=50000)
    assert plan.total_budget == 50000

    plan_no_budget = PlanSubmission(rationale="test")
    assert plan_no_budget.total_budget is None


# --- Blackboard + Checkpoint tests ---


def test_update_and_read_shared_context(tmp_path):
    runtime, token, store, _meta = _setup_session(tmp_path)

    anyio.run(
        lead_agent_tools.update_shared_context.handler,
        {"update": "Use SQLite for storage", "section": "DB decision"},
    )
    result = anyio.run(lead_agent_tools.read_shared_context_tool.handler, {})

    content = json.loads(result["content"][0]["text"])["shared_context"]
    assert content is not None
    assert "DB decision" in content
    assert "Use SQLite" in content

    events = store.load_events("lead-test")
    assert any(e.event_type == "tool.update_shared_context" for e in events)
    assert any(e.event_type == "tool.read_shared_context" for e in events)

    _teardown_session(token)


def test_check_checkpoints_empty(tmp_path):
    _runtime, token, _store, _meta = _setup_session(tmp_path)

    result = anyio.run(lead_agent_tools.check_checkpoints.handler, {})
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 0
    assert data["pending_checkpoints"] == []

    _teardown_session(token)


def test_check_checkpoints_with_pending(tmp_path):
    _runtime, token, _store, _meta = _setup_session(tmp_path)

    from openmax.task_file import write_checkpoint

    write_checkpoint(str(tmp_path), "api-task", "## Decision needed\nOption A or B")

    result = anyio.run(lead_agent_tools.check_checkpoints.handler, {})
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 1
    assert data["pending_checkpoints"][0]["task_name"] == "api-task"
    assert "Option A or B" in data["pending_checkpoints"][0]["content"]

    _teardown_session(token)


def test_resolve_checkpoint_sends_to_pane(tmp_path):
    runtime, token, store, _meta = _setup_session(tmp_path)

    # Add subtask with a known pane_id
    runtime.plan.subtasks.append(
        SubTask(
            name="api-task",
            agent_type="claude-code",
            prompt="p",
            status=TaskStatus.RUNNING,
            pane_id=101,
        )
    )

    from openmax.task_file import read_checkpoint, read_shared_context, write_checkpoint

    write_checkpoint(str(tmp_path), "api-task", "## Decision needed\nOption A or B")

    anyio.run(
        lead_agent_tools.resolve_checkpoint.handler,
        {"task_name": "api-task", "decision": "Use option A"},
    )

    # Checkpoint deleted
    assert read_checkpoint(str(tmp_path), "api-task") is None
    # Decision on blackboard
    bb = read_shared_context(str(tmp_path))
    assert bb is not None
    assert "Use option A" in bb
    # Sent to pane
    assert any(text == "Use option A" for (_, text) in runtime.pane_mgr.sent)

    events = store.load_events("lead-test")
    assert any(e.event_type == "tool.resolve_checkpoint" for e in events)

    _teardown_session(token)


def test_dispatch_injects_checkpoint_protocol(monkeypatch, tmp_path):
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "api-task", "agent_type": "generic", "prompt": "Build API"},
    )

    sent_prompt = runtime.pane_mgr.sent[0][1]
    assert "Checkpoint Protocol" in sent_prompt

    _teardown_session(token)


def test_dispatch_injects_blackboard(monkeypatch, tmp_path):
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    from openmax.task_file import append_shared_context

    append_shared_context(str(tmp_path), "Use SQLite", section="DB decision")

    anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "api-task", "agent_type": "generic", "prompt": "Build API"},
    )

    sent_prompt = runtime.pane_mgr.sent[0][1]
    assert "Shared Blackboard" in sent_prompt
    assert "Use SQLite" in sent_prompt

    _teardown_session(token)


def test_dispatch_with_role_reviewer(monkeypatch, tmp_path):
    """dispatch_agent with role='reviewer' injects reviewer context into prompt."""
    runtime, token, store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    result = anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {
            "task_name": "review-api",
            "agent_type": "generic",
            "prompt": "Review the API",
            "role": "reviewer",
        },
    )

    st = runtime.plan.subtasks[0]
    assert st.role == "reviewer"
    assert "Reviewer" in st.prompt
    assert "Do NOT commit" in st.prompt

    parsed = json.loads(result["content"][0]["text"])
    assert parsed["role"] == "reviewer"

    events = store.load_events("lead-test")
    dispatch_events = [e for e in events if e.event_type == "tool.dispatch_agent"]
    assert dispatch_events[0].payload["role"] == "reviewer"

    _teardown_session(token)


def test_dispatch_with_default_writer_role(monkeypatch, tmp_path):
    """dispatch_agent without role defaults to 'writer' with no extra context."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "write-api", "agent_type": "generic", "prompt": "Build API"},
    )

    st = runtime.plan.subtasks[0]
    assert st.role == "writer"
    assert "## Role:" not in st.prompt

    _teardown_session(token)


def test_dispatch_includes_cost_estimate(monkeypatch, tmp_path):
    """dispatch_agent response includes estimated cost and tokens."""
    runtime, token, store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)

    result = anyio.run(
        lead_agent_tools.dispatch_agent.handler,
        {"task_name": "api", "agent_type": "generic", "prompt": "Build the API"},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert "estimated_cost_usd" in parsed
    assert "estimated_tokens" in parsed
    assert parsed["estimated_cost_usd"] > 0
    assert parsed["estimated_tokens"] > 0

    st = runtime.plan.subtasks[0]
    assert st.estimated_cost_usd is not None
    assert st.estimated_cost_usd > 0

    events = store.load_events("lead-test")
    dispatch_events = [e for e in events if e.event_type == "tool.dispatch_agent"]
    assert dispatch_events[0].payload["estimated_cost_usd"] > 0

    _teardown_session(token)


def test_budget_hard_limit_includes_stop_action(monkeypatch, tmp_path):
    """read_pane_output with budget at hard limit includes action: stop_agent."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    runtime.plan.subtasks.append(
        SubTask(
            name="expensive-task",
            agent_type="generic",
            prompt="Do work",
            status=TaskStatus.RUNNING,
            pane_id=101,
            token_budget=1000,
            tokens_used=1000,
        )
    )

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert parsed["budget"]["warning"] == "hard_limit"
    assert parsed["budget"]["action"] == "stop_agent"

    _teardown_session(token)


def test_budget_soft_limit_no_stop_action(monkeypatch, tmp_path):
    """read_pane_output with budget at soft limit does NOT include stop action."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    runtime.plan.subtasks.append(
        SubTask(
            name="moderate-task",
            agent_type="generic",
            prompt="Do work",
            status=TaskStatus.RUNNING,
            pane_id=101,
            token_budget=1000,
            tokens_used=850,
        )
    )

    result = anyio.run(
        lead_agent_tools.read_pane_output.handler,
        {"pane_id": 101},
    )

    parsed = json.loads(result["content"][0]["text"])
    assert parsed["budget"]["warning"] == "soft_limit"
    assert "action" not in parsed["budget"]

    _teardown_session(token)


# ── _launch_pane fallback tests ────────────────────────────────────────────


def test_launch_pane_falls_back_to_new_window_on_no_space(tmp_path):
    """When add_pane raises 'No space for split!', _launch_pane creates a new window."""
    from openmax.lead_agent.tools._helpers import _launch_pane
    from openmax.pane_backend import PaneBackendError

    runtime, token, *_ = _setup_session(tmp_path)
    runtime.agent_window_id = 7  # existing window already set

    new_window_pane = SimpleNamespace(pane_id=999, window_id=42)

    def add_pane_no_space(**kwargs):
        raise PaneBackendError("kaku split-pane failed: Error: No space for split!")

    runtime.pane_mgr.add_pane = add_pane_no_space
    runtime.pane_mgr.create_window = lambda **kw: new_window_pane

    try:
        pane = _launch_pane(runtime, ["bash"], "test-task", "command")
    finally:
        reset_lead_agent_runtime(token)

    assert pane.pane_id == 999
    assert runtime.agent_window_id == 42  # updated to new window


def test_launch_pane_reraises_unrelated_pane_backend_error(tmp_path):
    """PaneBackendError not about space must propagate — not silently swallowed."""
    import pytest

    from openmax.lead_agent.tools._helpers import _launch_pane
    from openmax.pane_backend import PaneBackendError

    runtime, token, *_ = _setup_session(tmp_path)
    runtime.agent_window_id = 7

    def add_pane_broken(**kwargs):
        raise PaneBackendError("kaku split-pane failed: timeout")

    runtime.pane_mgr.add_pane = add_pane_broken

    try:
        with pytest.raises(PaneBackendError, match="timeout"):
            _launch_pane(runtime, ["bash"], "test-task", "command")
    finally:
        reset_lead_agent_runtime(token)


# ── Auto Agent Selection Tests ─────────────────────────────────────────


def test_auto_select_agent_both_available(tmp_path):
    """When both claude-code and codex are available, role determines agent."""
    from openmax.lead_agent.tools._dispatch import _auto_select_agent

    runtime, token, _store, _meta = _setup_session(tmp_path)
    runtime.allowed_agents = ["claude-code", "codex"]

    try:
        assert _auto_select_agent(runtime, "writer") == "codex"
        assert _auto_select_agent(runtime, "reviewer") == "claude-code"
        assert _auto_select_agent(runtime, "challenger") == "claude-code"
        assert _auto_select_agent(runtime, "debugger") == "claude-code"
    finally:
        _teardown_session(token)


def test_auto_select_agent_single(tmp_path):
    """When only one agent is available, always returns that agent."""
    from openmax.lead_agent.tools._dispatch import _auto_select_agent

    runtime, token, _store, _meta = _setup_session(tmp_path)

    runtime.allowed_agents = ["codex"]
    try:
        assert _auto_select_agent(runtime, "writer") == "codex"
        assert _auto_select_agent(runtime, "reviewer") == "codex"
    finally:
        _teardown_session(token)


def test_auto_select_agent_no_allowed_uses_registry_default(tmp_path):
    """When allowed_agents is empty, falls back to registry default."""
    from openmax.lead_agent.tools._dispatch import _auto_select_agent

    runtime, token, _store, _meta = _setup_session(tmp_path)
    runtime.allowed_agents = None

    try:
        result = _auto_select_agent(runtime, "writer")
        assert result == "claude-code"  # registry default
    finally:
        _teardown_session(token)


def test_dispatch_agent_auto_selects_without_explicit_type(monkeypatch, tmp_path):
    """dispatch_agent without agent_type auto-infers from role."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)
    runtime.allowed_agents = ["claude-code", "codex"]

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "review-task", "prompt": "Review code", "role": "reviewer"},
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["agent_type"] == "claude-code"

        result2 = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {"task_name": "write-task", "prompt": "Write code"},
        )
        parsed2 = json.loads(result2["content"][0]["text"])
        assert parsed2["agent_type"] == "codex"
    finally:
        _teardown_session(token)


def test_dispatch_agent_explicit_type_overrides_auto(monkeypatch, tmp_path):
    """Explicitly passing agent_type overrides auto-selection."""
    runtime, token, _store, _meta = _setup_session(tmp_path)
    _patch_time(monkeypatch)
    runtime.allowed_agents = ["claude-code", "codex"]

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "write-with-claude",
                "agent_type": "claude-code",
                "prompt": "Write code",
                "role": "writer",
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["agent_type"] == "claude-code"
    finally:
        _teardown_session(token)


def test_planned_subtask_agent_type():
    """PlannedSubtask accepts and stores agent_type."""
    from openmax.lead_agent.types import PlannedSubtask

    st = PlannedSubtask(name="api", description="Build API", agent_type="codex")
    assert st.agent_type == "codex"

    st_none = PlannedSubtask(name="api", description="Build API")
    assert st_none.agent_type is None


def test_submit_plan_accepts_agent_type(monkeypatch, tmp_path):
    """submit_plan accepts agent_type in subtask items."""
    runtime, token, _store, _meta = _setup_session(tmp_path)

    try:
        result = anyio.run(
            lead_agent_tools.submit_plan.handler,
            {
                "subtasks": [
                    {
                        "name": "research",
                        "description": "Investigate codebase",
                        "agent_type": "claude-code",
                    },
                    {
                        "name": "implement",
                        "description": "Write the feature",
                        "agent_type": "codex",
                        "dependencies": ["research"],
                    },
                ],
                "rationale": "Research first, then implement",
                "parallel_groups": [],
            },
        )
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "accepted"
    finally:
        _teardown_session(token)
