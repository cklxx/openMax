from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

import openmax.session_runtime as session_runtime
from openmax import cli
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import LeadAgentStartupError
from openmax.memory import MemoryStore
from openmax.pane_backend import HeadlessPaneBackend, KakuPaneBackend
from openmax.session_runtime import SessionStore, anchor_payload


class DummyPaneManager:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def summary(self) -> dict:
        return {"total_windows": 0, "done": 0}

    def cleanup_all(self) -> None:
        return None


def test_help():
    runner = CliRunner()

    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "orchestration" in result.output.lower()


def test_run_help():
    runner = CliRunner()

    result = runner.invoke(cli.main, ["run", "--help"])

    assert result.exit_code == 0
    assert "--keep-panes" in result.output
    assert "--session-id" in result.output
    assert "--agents" in result.output
    assert "--pane-backend" in result.output


def test_resume_requires_session_id():
    runner = CliRunner()

    result = runner.invoke(cli.main, ["run", "Build feature", "--resume"])

    assert result.exit_code != 0
    assert "--resume requires --session-id" in result.output


def test_run_forwards_session_options(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "run",
            "Build feature",
            "--cwd",
            str(tmp_path),
            "--session-id",
            "sess-123",
            "--resume",
        ],
    )

    assert result.exit_code == 0
    assert captured["session_id"] == "sess-123"
    assert captured["resume"] is True
    assert captured["cwd"] == str(tmp_path.resolve())


def test_run_uses_headless_backend_without_checking_kaku(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "ensure_kaku",
        lambda: (_ for _ in ()).throw(AssertionError("ensure_kaku should not run")),
    )
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--pane-backend", "headless"],
    )

    assert result.exit_code == 0
    assert isinstance(captured["pane_mgr"]._backend, HeadlessPaneBackend)


def test_run_uses_kaku_backend_by_default(monkeypatch, tmp_path):
    ensure_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "ensure_kaku",
        lambda: ensure_calls.append("called") or True,
    )
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["run", "Build feature", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert ensure_calls == ["called"]
    assert isinstance(captured["pane_mgr"]._backend, KakuPaneBackend)


def test_agents_option_rejects_unknown_type(monkeypatch):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    runner = CliRunner()

    result = runner.invoke(cli.main, ["run", "Build feature", "--agents", "claude-code,unknown"])

    assert result.exit_code != 0
    assert "Unknown agent type" in result.output


def test_agents_option_forwarded(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--agents", "codex,claude-code"],
    )

    assert result.exit_code == 0
    assert captured["allowed_agents"] == ["codex", "claude-code"]


def test_agents_option_deduplicates_and_ignores_empty_values(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--agents", "codex, ,codex,claude-code"],
    )

    assert result.exit_code == 0
    assert captured["allowed_agents"] == ["codex", "claude-code"]


def test_agents_option_accepts_configured_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    registry = built_in_agent_registry().with_definition(
        AgentDefinition(
            name="remote-codex",
            adapter=built_in_agent_registry().get("codex"),
            source="test",
            built_in=False,
        )
    )
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: registry)

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--agents", "remote-codex,codex"],
    )

    assert result.exit_code == 0
    assert captured["allowed_agents"] == ["remote-codex", "codex"]


def test_run_exits_non_zero_on_lead_agent_startup_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    def fake_run_lead_agent(**kwargs):
        raise LeadAgentStartupError(
            category="authentication",
            stage="sdk_client_startup",
            detail="Authentication required",
            remediation="Run `claude auth login` and retry.",
        )

    monkeypatch.setattr(cli, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["run", "Build feature", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "Done." not in result.output
    assert "Closing managed panes" in result.output


def test_memories_command_prints_workspace_memory(monkeypatch, tmp_path):
    store = MemoryStore(base_dir=tmp_path / "memory")
    cwd = str(tmp_path / "workspace")
    store.record_lesson(
        cwd=cwd,
        task="Build API",
        lesson="Prefer codex for API endpoints.",
        confidence=8,
    )
    monkeypatch.setattr(cli, "MemoryStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["memories", "--cwd", cwd])

    assert result.exit_code == 0
    assert "Prefer codex for API endpoints." in result.output


def test_recommend_agents_command_prints_rankings(monkeypatch, tmp_path):
    store = MemoryStore(base_dir=tmp_path / "memory")
    cwd = str(tmp_path / "workspace")
    store.record_run_summary(
        cwd=cwd,
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
    monkeypatch.setattr(cli, "MemoryStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["recommend-agents", "Refactor API endpoints", "--cwd", cwd])

    assert result.exit_code == 0
    assert "codex" in result.output


def test_recommendation_eval_command_prints_offline_metrics(monkeypatch, tmp_path):
    store = MemoryStore(base_dir=tmp_path / "memory")
    cwd = str(tmp_path / "workspace")
    store.record_run_summary(
        cwd=cwd,
        task="Build src/api/routes.py endpoints",
        notes="API route outcomes.",
        completion_pct=100,
        subtasks=[
            {
                "name": "API routes",
                "agent_type": "codex",
                "status": "done",
                "prompt": "Update src/api/routes.py",
            }
        ],
        anchors=[{"summary": "API work succeeded in src/api/routes.py"}],
    )
    store.record_run_summary(
        cwd=cwd,
        task="Add tests for src/api/routes.py",
        notes="API test outcomes.",
        completion_pct=100,
        subtasks=[
            {
                "name": "API tests",
                "agent_type": "codex",
                "status": "done",
                "prompt": "Update tests/test_routes.py",
            }
        ],
        anchors=[{"summary": "API test work stayed near src/api/routes.py"}],
    )
    monkeypatch.setattr(cli, "MemoryStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["recommendation-eval", "--cwd", cwd])

    assert result.exit_code == 0
    assert "Offline recommendation eval" in result.output
    assert "Strategy:" in result.output
    assert "Baseline:" in result.output
    assert "coverage=100%" in result.output
    assert "hit_rate=100%" in result.output
    assert "hit_rate_lift=" in result.output


def test_list_agents_includes_configured_agents(monkeypatch, tmp_path):
    registry = built_in_agent_registry().with_definition(
        AgentDefinition(
            name="remote-codex",
            adapter=built_in_agent_registry().get("codex"),
            source="/tmp/agents.toml",
            built_in=False,
        )
    )
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: registry)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list-agents", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "remote-codex" in result.output


def test_runs_command_prints_recent_sessions(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    older = store.create_session("session-old", "Older task", str(tmp_path / "older"))
    older.latest_phase = "plan"
    store._write_meta(older)
    store.append_event(
        older,
        "tool.report_completion",
        {"completion_pct": 25, "notes": "Started"},
    )

    newer = store.create_session("session-new", "Newer task", str(tmp_path / "newer"))
    newer.latest_phase = "monitor"
    store._write_meta(newer)
    store.append_event(
        newer,
        "tool.report_completion",
        {"completion_pct": 75, "notes": "Almost there"},
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs"])

    assert result.exit_code == 0
    assert "Recent sessions" in result.output
    assert result.output.index("session-new") < result.output.index("session-old")
    assert "completion=75%" in result.output
    assert "phase=monitor" in result.output


def test_runs_command_prints_scorecard_signals(monkeypatch, tmp_path):
    timestamps = iter(
        [
            "2026-03-13T12:01:00+00:00",
            "2026-03-13T12:02:00+00:00",
            "2026-03-13T12:03:00+00:00",
        ]
    )
    monkeypatch.setattr(session_runtime, "utc_now_iso", lambda: next(timestamps))

    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-scorecard", "Build API", str(tmp_path / "workspace"))
    meta.status = "completed"
    meta.latest_phase = "monitor"
    meta.created_at = "2026-03-13T12:00:00+00:00"
    meta.updated_at = "2026-03-13T12:00:00+00:00"
    store._write_meta(meta)
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(meta, "tool.send_text_to_pane", {"pane_id": 11, "text": "Re-run the tests"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs", "--limit", "1"])

    assert result.exit_code == 0
    assert "session-scorecard | completed | phase=monitor" in result.output
    assert (
        "scorecard=status=completed | completion=100% | duration=180s | "
        "subtasks=0/1 done | interventions=1 | startup_failure=n/a" in result.output
    )


def test_runs_command_handles_empty_store(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs"])

    assert result.exit_code == 0
    assert "No sessions found." in result.output


def test_runs_command_filters_by_status_and_limit(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    completed = store.create_session("session-completed", "Completed task", str(tmp_path / "done"))
    active_old = store.create_session("session-active-old", "Older active", str(tmp_path / "older"))
    active_new = store.create_session(
        "session-active-new", "Newest active", str(tmp_path / "newer")
    )
    failed = store.create_session("session-failed", "Failed task", str(tmp_path / "failed"))

    ordered = [
        (completed, "completed", "2026-03-13T07:00:00+00:00"),
        (active_old, "active", "2026-03-13T08:00:00+00:00"),
        (active_new, "active", "2026-03-13T09:00:00+00:00"),
        (failed, "failed", "2026-03-13T10:00:00+00:00"),
    ]
    for meta, status, updated_at in ordered:
        meta.status = status
        meta.updated_at = updated_at
        store._write_meta(meta)

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs", "--status", "active", "--limit", "1"])

    assert result.exit_code == 0
    assert "session-active-new" in result.output
    assert "session-active-old" not in result.output
    assert "session-completed" not in result.output
    assert "session-failed" not in result.output


def test_runs_command_rejects_invalid_status(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs", "--status", "broken"])

    assert result.exit_code != 0
    assert "Invalid value for '--status'" in result.output


def test_inspect_command_prints_reconstructed_session(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-a", "Build API", str(tmp_path / "workspace"))
    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(
            phase="plan",
            summary="Defined two workstreams",
            tasks=[
                {
                    "name": "API routes",
                    "agent_type": "codex",
                    "prompt": "Implement API routes",
                    "status": "running",
                    "pane_id": 11,
                    "pane_history": [11],
                }
            ],
            completion_pct=40,
        ),
    )
    store.append_event(meta, "tool.mark_task_done", {"task_name": "API routes"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-a"])

    assert result.exit_code == 0
    assert "Session: session-a" in result.output
    assert "Task: Build API" in result.output
    assert "latest_phase=plan" in result.output
    assert "completion=100%" in result.output
    assert "Defined two workstreams" in result.output
    assert "API routes | done | codex | pane=11" in result.output


def test_inspect_command_prints_richer_completed_timeline(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-completed", "Build API", str(tmp_path / "workspace"))
    meta.status = "completed"
    store._write_meta(meta)
    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(
            phase="plan",
            summary="Defined two workstreams",
            tasks=[
                {
                    "name": "API routes",
                    "agent_type": "codex",
                    "prompt": "Implement API routes",
                    "status": "running",
                    "pane_id": 11,
                    "pane_history": [11],
                }
            ],
            completion_pct=40,
        ),
    )
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(meta, "tool.read_pane_output", {"pane_id": 11})
    store.append_event(meta, "tool.mark_task_done", {"task_name": "API routes"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )
    store.append_event(meta, "session.completed", {"total_subtasks": 1, "done_subtasks": 1})

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-completed"])

    assert result.exit_code == 0
    assert "Outcome" in result.output
    assert "status=completed" in result.output
    assert "summary=Session completed" in result.output
    assert "Recent activity" in result.output
    assert "Read pane 11 output" in result.output
    assert "Reported completion at 100%" in result.output
    assert "Anchors" in result.output
    assert "Subtasks" in result.output


def test_inspect_command_prints_run_scorecard(monkeypatch, tmp_path):
    timestamps = iter(
        [
            "2026-03-13T12:01:00+00:00",
            "2026-03-13T12:02:00+00:00",
            "2026-03-13T12:03:00+00:00",
            "2026-03-13T12:04:00+00:00",
        ]
    )
    monkeypatch.setattr(session_runtime, "utc_now_iso", lambda: next(timestamps))

    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-scorecard", "Build API", str(tmp_path / "workspace"))
    meta.status = "completed"
    meta.created_at = "2026-03-13T12:00:00+00:00"
    meta.updated_at = "2026-03-13T12:00:00+00:00"
    store._write_meta(meta)
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(meta, "tool.send_text_to_pane", {"pane_id": 11, "text": "Re-run the tests"})
    store.append_event(meta, "tool.mark_task_done", {"task_name": "API routes"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-scorecard"])

    assert result.exit_code == 0
    assert "Scorecard" in result.output
    assert "status=completed | completion=100% | duration=240s" in result.output
    assert "subtasks=1/1 done | interventions=1 | startup_failure=n/a" in result.output


def test_inspect_command_prints_failure_summary_for_aborted_session(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-aborted", "Build UI", str(tmp_path / "workspace"))
    meta.status = "aborted"
    store._write_meta(meta)
    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(
            phase="monitor",
            summary="Waiting on UI validation",
            tasks=[],
            completion_pct=60,
        ),
    )
    store.append_event(
        meta,
        "session.aborted",
        {"reason": "Operator cancelled after validation stalled"},
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-aborted"])

    assert result.exit_code == 0
    assert "status=aborted" in result.output
    assert "summary=Session aborted: Operator cancelled after validation stalled" in result.output
    assert "Recent activity" in result.output
    assert "Waiting on UI validation" in result.output


def test_inspect_command_prints_failure_summary_for_startup_failed_session(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-failed", "Bootstrap me", str(tmp_path / "workspace"))
    meta.status = "failed"
    store._write_meta(meta)
    store.append_event(
        meta,
        "session.startup_failed",
        {
            "category": "authentication",
            "stage": "sdk_client_startup",
            "detail": "Authentication required",
            "remediation": "Run `claude auth login` and retry.",
        },
    )

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-failed"])

    assert result.exit_code == 0
    assert "status=failed" in result.output
    assert (
        "summary=Lead agent startup failed [authentication] during sdk_client_startup: "
        "Authentication required" in result.output
    )
    assert "Recent activity" in result.output


def test_inspect_command_reports_missing_session(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "missing-session"])

    assert result.exit_code != 0
    assert "Session 'missing-session' was not found." in result.output


def test_runs_command_surfaces_event_log_warnings(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-corrupt", "Build API", str(tmp_path / "workspace"))
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 50, "notes": "Halfway there"},
    )
    with store._events_path(meta.session_id).open("a", encoding="utf-8") as file_obj:
        file_obj.write("{bad json\n")

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["runs"])

    assert result.exit_code == 0
    assert "session-corrupt | active" in result.output
    assert "warnings=Skipped 1 malformed event line while loading session history." in result.output


def test_inspect_command_prints_event_log_warnings(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    meta = store.create_session("session-corrupt", "Build API", str(tmp_path / "workspace"))
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 50, "notes": "Halfway there"},
    )
    with store._events_path(meta.session_id).open("a", encoding="utf-8") as file_obj:
        file_obj.write("{bad json\n")

    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "session-corrupt"])

    assert result.exit_code == 0
    assert "Diagnostics" in result.output
    assert "Skipped 1 malformed event line while loading session history." in result.output
