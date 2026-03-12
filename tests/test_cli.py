from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from openmax import cli
from openmax.memory_system import MemoryStore


class DummyPaneManager:
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


def test_resume_requires_session_id():
    runner = CliRunner()

    result = runner.invoke(cli.main, ["run", "Build feature", "--resume"])

    assert result.exit_code != 0
    assert "--resume requires --session-id" in result.output


def test_run_forwards_session_options(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

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


def test_agents_option_rejects_unknown_type():
    runner = CliRunner()

    result = runner.invoke(cli.main, ["run", "Build feature", "--agents", "claude-code,unknown"])

    assert result.exit_code != 0
    assert "Unknown agent type" in result.output


def test_agents_option_forwarded(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ensure_kaku", lambda: True)
    monkeypatch.setattr(cli, "PaneManager", DummyPaneManager)

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
