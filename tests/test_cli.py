from __future__ import annotations

import json
from types import SimpleNamespace

from click.testing import CliRunner

import openmax.lead_agent as lead_agent_mod
import openmax.session_runtime as session_runtime
from openmax import cli
from openmax.agent_registry import AgentDefinition, built_in_agent_registry
from openmax.lead_agent import LeadAgentStartupError
from openmax.pane_backend import HeadlessPaneBackend, KakuPaneBackend
from openmax.session_runtime import SessionStore, anchor_payload


class DummyPaneManager:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def list_all_panes():
        return []

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
    assert "--no-confirm" in result.output


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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

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


def test_run_generates_session_id_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "run",
            "Build feature",
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert isinstance(captured["session_id"], str)
    assert captured["session_id"].startswith("run-")


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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--pane-backend", "headless"],
    )

    assert result.exit_code == 0
    assert isinstance(captured["pane_mgr"]._backend, HeadlessPaneBackend)


def test_run_uses_kaku_backend_by_default(monkeypatch, tmp_path):
    ensure_calls: list[str] = []
    monkeypatch.setattr("openmax.terminal.is_kaku_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "ensure_kaku",
        lambda: ensure_calls.append("called") or True,
    )
    monkeypatch.setattr(cli, "resolve_pane_backend_name", lambda name=None: "kaku")
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--agents", "remote-codex,codex"],
    )

    assert result.exit_code == 0
    assert captured["allowed_agents"] == ["remote-codex", "codex"]


def test_run_no_confirm_flag_forwards_plan_confirm_false(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "--no-confirm"],
    )

    assert result.exit_code == 0
    assert captured["plan_confirm"] is False


def test_run_accepts_verbose_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path), "-v"],
    )

    assert result.exit_code == 0
    assert captured["verbose"] is True


def test_run_default_plan_confirm_true(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict[str, object] = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "Build feature", "--cwd", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured["plan_confirm"] is True


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

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["run", "Build feature", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "Done." not in result.output
    assert "Closing panes" in result.output


def test_setup_registers_openmax_mcp_servers_and_merges_existing_config(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "has_claude_auth", lambda: (True, "token present"))
    monkeypatch.setattr(
        cli,
        "run_claude_setup_token",
        lambda: (_ for _ in ()).throw(AssertionError("setup-token should not run")),
    )
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"claude", "codex"} else None,
    )

    claude_config_path = home_dir / ".claude.json"
    claude_config_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "existing": {
                        "type": "stdio",
                        "command": "existing-mcp",
                        "args": ["--flag"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    codex_config_path = home_dir / ".codex" / "config.toml"
    codex_config_path.parent.mkdir(parents=True)
    codex_config_path.write_text(
        'model = "gpt-5.4"\n\n[mcp_servers.existing]\ncommand = "existing-mcp"\n',
        encoding="utf-8",
    )

    def fake_subprocess_run(args, capture_output, text, timeout):
        assert args == ["codex", "mcp", "add", "openmax", "--", "openmax-mcp"]
        assert capture_output is True
        assert text is True
        assert timeout == 15
        existing = codex_config_path.read_text(encoding="utf-8")
        codex_config_path.write_text(
            existing + '\n[mcp_servers.openmax]\ncommand = "openmax-mcp"\n',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="Added global MCP server 'openmax'.", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_subprocess_run)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["setup"])

    assert result.exit_code == 0
    data = json.loads(claude_config_path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["mcpServers"]["existing"]["command"] == "existing-mcp"
    assert data["mcpServers"]["openmax"] == {
        "type": "stdio",
        "command": "openmax-mcp",
        "args": [],
    }
    codex_config = codex_config_path.read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in codex_config
    assert '[mcp_servers.existing]\ncommand = "existing-mcp"' in codex_config
    assert '[mcp_servers.openmax]\ncommand = "openmax-mcp"' in codex_config


def test_setup_skips_codex_registration_when_codex_cli_missing(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "has_claude_auth", lambda: (True, "token present"))
    monkeypatch.setattr(
        cli,
        "run_claude_setup_token",
        lambda: (_ for _ in ()).throw(AssertionError("setup-token should not run")),
    )
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("codex mcp add should not run when codex is missing")
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["setup"])

    assert result.exit_code == 0
    assert "skipped Codex MCP registration" in result.output


def test_setup_does_not_reregister_codex_mcp_when_already_present(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "has_claude_auth", lambda: (True, "token present"))
    monkeypatch.setattr(
        cli,
        "run_claude_setup_token",
        lambda: (_ for _ in ()).throw(AssertionError("setup-token should not run")),
    )
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"claude", "codex"} else None,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("codex mcp add should not run when already configured")
        ),
    )

    (home_dir / ".codex").mkdir()
    (home_dir / ".codex" / "config.toml").write_text(
        '[mcp_servers.openmax]\ncommand = "openmax-mcp"\n',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["setup"])

    assert result.exit_code == 0
    assert "Codex MCP server already registered" in result.output


def test_agents_includes_configured_agents(monkeypatch, tmp_path):
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
    result = runner.invoke(cli.main, ["agents", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "remote-codex" in result.output


def test_sessions_command_prints_recent_sessions(monkeypatch, tmp_path):
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
    result = runner.invoke(cli.main, ["sessions"])

    assert result.exit_code == 0
    assert result.output.index("session-new") < result.output.index("session-old")
    assert "75%" in result.output
    assert "monitor" in result.output


def test_sessions_command_prints_scorecard_signals(monkeypatch, tmp_path):
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
    result = runner.invoke(cli.main, ["sessions", "--limit", "1"])

    assert result.exit_code == 0
    assert "session-scor" in result.output  # truncated session ID in table
    assert "completed" in result.output
    assert "monitor" in result.output


def test_sessions_command_handles_empty_store(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["sessions"])

    assert result.exit_code == 0
    assert "No sessions found." in result.output


def test_sessions_command_filters_by_status_and_limit(monkeypatch, tmp_path):
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
    result = runner.invoke(cli.main, ["sessions", "--status", "active", "--limit", "1"])

    assert result.exit_code == 0
    assert "session-acti" in result.output  # session-active-new truncated
    assert "Newest active" in result.output
    # Only 1 active session shown (limit=1), others excluded
    assert "Completed task" not in result.output
    assert "Failed task" not in result.output


def test_sessions_command_rejects_invalid_status(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["sessions", "--status", "broken"])

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
    assert "session-a" in result.output
    assert "Build API" in result.output
    assert "plan" in result.output
    assert "100%" in result.output
    assert "Defined two workstreams" in result.output
    assert "API routes" in result.output
    assert "codex" in result.output


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
    assert "completed" in result.output
    assert "Session completed" in result.output
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
    assert "scorecard" in result.output.lower()
    assert "status=completed" in result.output
    assert "completion=100%" in result.output
    assert "subtasks=1/1 done" in result.output


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
    assert "aborted" in result.output
    assert "Operator cancelled after validation stalled" in result.output
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
    assert "failed" in result.output
    assert "Authentication required" in result.output
    assert "Recent activity" in result.output


def test_inspect_command_reports_missing_session(monkeypatch, tmp_path):
    store = SessionStore(base_dir=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["inspect", "missing-session"])

    assert result.exit_code != 0
    assert "Session 'missing-session' was not found." in result.output


def test_sessions_command_surfaces_event_log_warnings(monkeypatch, tmp_path):
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
    result = runner.invoke(cli.main, ["sessions"])

    assert result.exit_code == 0
    assert "session-corr" in result.output  # truncated in table
    assert "active" in result.output
    assert "Build API" in result.output


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


# ── loop command ──────────────────────────────────────────────────────────────


def test_loop_help():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["loop", "--help"])
    assert result.exit_code == 0
    assert "--max-iterations" in result.output
    assert "--delay" in result.output


def test_loop_first_iteration_gets_no_loop_context(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    captured: list[dict] = []

    def fake_run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run)

    import openmax.loop_session as lsmod

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    runner.invoke(cli.main, ["loop", "improve openmax", "--max-iterations", "1", "--delay", "0"])

    assert len(captured) == 1
    assert captured[0]["loop_context"] is None or captured[0]["loop_context"] == ""


def test_loop_second_iteration_receives_loop_context(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    captured: list[dict] = []

    def fake_run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run)

    import openmax.loop_session as lsmod

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    runner.invoke(cli.main, ["loop", "improve openmax", "--max-iterations", "2", "--delay", "0"])

    assert len(captured) == 2
    # Second iteration must carry prior-iteration context
    ctx2 = captured[1]["loop_context"]
    assert ctx2 is not None and ctx2 != ""
    assert "Iteration 2" in ctx2
    assert "DO NOT repeat" in ctx2


def test_loop_generates_session_id_for_each_iteration(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    captured: list[dict] = []

    def fake_run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run)

    import openmax.loop_session as lsmod

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["loop", "goal", "--max-iterations", "2", "--delay", "0"])

    assert result.exit_code == 0
    assert len(captured) == 2
    assert isinstance(captured[0]["session_id"], str)
    assert isinstance(captured[1]["session_id"], str)
    assert captured[0]["session_id"].startswith("loop-1-")
    assert captured[1]["session_id"].startswith("loop-2-")
    assert captured[0]["session_id"] != captured[1]["session_id"]


def test_loop_writes_tape_entry_per_iteration(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", lambda **kw: SimpleNamespace(subtasks=[]))

    import openmax.loop_session as lsmod

    written: list[tuple[str, str | None]] = []
    orig_append = lsmod.LoopSessionStore.append_iteration

    def spy_append(self, loop_id, iteration):
        written.append((loop_id, iteration.session_id))
        orig_append(self, loop_id, iteration)

    monkeypatch.setattr(lsmod.LoopSessionStore, "append_iteration", spy_append)

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    runner.invoke(cli.main, ["loop", "goal", "--max-iterations", "3", "--delay", "0"])

    assert len(written) == 3  # one tape entry per iteration
    assert all(loop_id for loop_id, _session_id in written)
    assert all(session_id and session_id.startswith("loop-") for _loop_id, session_id in written)


def test_make_loop_iteration_preserves_session_id():
    iteration = cli._make_loop_iteration(
        1, "2026-03-21T00:00:00+00:00", None, session_id="loop-1-test"
    )

    assert iteration.session_id == "loop-1-test"
    assert iteration.completion_pct == 0


def test_loop_stops_at_max_iterations(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    call_count = 0

    def fake_run(**kwargs):
        nonlocal call_count
        call_count += 1
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run)

    import openmax.loop_session as lsmod

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["loop", "goal", "--max-iterations", "4", "--delay", "0"])

    assert result.exit_code == 0
    assert call_count == 4


def test_loop_handles_startup_error_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

    from openmax.lead_agent import LeadAgentStartupError

    def fake_run(**kwargs):
        raise LeadAgentStartupError(
            category="bootstrap",
            stage="sdk_client_startup",
            detail="mock failure",
            remediation="retry",
        )

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run)

    import openmax.loop_session as lsmod

    def patched_dir():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(lsmod, "_loops_dir", patched_dir)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["loop", "goal", "--max-iterations", "1", "--delay", "0"])

    assert result.exit_code == 0  # graceful, not a crash


# ── DefaultGroup fallback + pane reuse ────────────────────────────────────────


def _make_pane_info(pane_id: int, window_id: int, title: str = "", cwd: str = "/tmp"):
    from openmax.pane_backend import PaneInfo

    return PaneInfo(
        window_id=window_id,
        tab_id=1,
        pane_id=pane_id,
        workspace="",
        rows=24,
        cols=80,
        title=title,
        cwd=cwd,
        is_active=False,
        is_zoomed=False,
        cursor_visibility="visible",
    )


def test_bare_task_routes_to_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    captured: dict = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["Build feature", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert captured["task"] == "Build feature"


def test_run_attaches_existing_panes(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_agent_registry", lambda cwd: built_in_agent_registry())

    fake_panes = [_make_pane_info(7, 2, "claude", "/repo")]

    class DummyAttachPaneManager(DummyPaneManager):
        @staticmethod
        def list_all_panes():
            return fake_panes

        def attach_pane(self, pane_info, purpose: str):
            pass

        def get_text(self, pane_id: int) -> str:
            return "$ echo hello"

    monkeypatch.setattr(cli, "PaneManager", DummyAttachPaneManager)

    captured: dict = {}

    def fake_run_lead_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(lead_agent_mod, "run_lead_agent", fake_run_lead_agent)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["run", "monitor all panes", "--cwd", str(tmp_path), "--pane-backend", "headless"],
    )

    assert result.exit_code == 0
    assert captured["task"] == "monitor all panes"
    assert "Attached Existing Panes" in captured["loop_context"]
    assert "pane_id=7" in captured["loop_context"]


# ── Grouped help output ───────────────────────────────────────────────────────


def test_help_shows_grouped_commands():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "Run:" in result.output
    assert "Sessions:" in result.output
    assert "Environment:" in result.output
    assert "Setup:" in result.output
    assert "Benchmark:" in result.output


def test_help_shows_exactly_13_visible_commands():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    expected = [
        "run",
        "loop",
        "sessions",
        "inspect",
        "usage",
        "log",
        "status",
        "agents",
        "panes",
        "models",
        "setup",
        "doctor",
        "benchmark",
    ]
    for cmd in expected:
        assert cmd in result.output


def test_msg_hidden_from_help():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    lines = result.output.splitlines()
    assert not any(line.strip().startswith("msg ") for line in lines)


def test_msg_still_works():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["msg", "--help"])
    assert result.exit_code == 0


# ── panes read by ID ─────────────────────────────────────────────────────────


def test_panes_read_by_id(monkeypatch):
    def _get_text(self, pid):
        return f"output of pane {pid}"

    pm = type("PM", (), {"__init__": lambda *a, **kw: None, "get_text": _get_text})
    monkeypatch.setattr(cli, "PaneManager", pm)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["panes", "5"])

    assert result.exit_code == 0
    assert "output of pane 5" in result.output


# ── log command ───────────────────────────────────────────────────────────────


def test_log_replay(tmp_path):
    import json as _json

    log_dir = tmp_path / ".openmax"
    log_dir.mkdir()
    log_file = log_dir / "messages-test-session.jsonl"
    log_file.write_text(
        _json.dumps({"_ts": 100.0, "type": "progress", "task": "build", "pct": 50}) + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["log", "--session", "test-session", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "build" in result.output
    assert "50%" in result.output


def test_log_missing_file(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["log", "--session", "nonexistent", "--cwd", str(tmp_path)])

    assert result.exit_code != 0


# ── setup --skills ────────────────────────────────────────────────────────────


def test_setup_skills_flag(monkeypatch, tmp_path):
    installed: list[str] = []

    def fake_install(target):
        installed.append(str(target))
        return [str(target / "openmax.md")]

    monkeypatch.setattr("openmax.skills.install", fake_install)
    monkeypatch.setattr("openmax.skills.project_commands_dir", lambda cwd=None: tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["setup", "--skills"])

    assert result.exit_code == 0
    assert len(installed) == 1
