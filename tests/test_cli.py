from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from openmax import cli


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
