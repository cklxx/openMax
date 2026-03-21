from __future__ import annotations

import subprocess

from openmax.mailbox import SessionMailbox
from openmax.mcp_server import execute_with_codex, report_done, report_progress


def test_report_done_sends_message_to_mailbox(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-done")
    mailbox = SessionMailbox("mcp-done", tmp_path)
    mailbox.start()
    try:
        result = report_done("API task", "implemented endpoint")
        assert result["ok"] is True
        assert result["session_id"] == "mcp-done"

        message = mailbox.receive(timeout=2.0)
        assert message is not None
        assert message.raw == {
            "type": "done",
            "task": "API task",
            "summary": "implemented endpoint",
        }
    finally:
        mailbox.stop()


def test_report_progress_sends_message_to_mailbox(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-progress")
    mailbox = SessionMailbox("mcp-progress", tmp_path)
    mailbox.start()
    try:
        result = report_progress("API task", 55, "wiring handlers")
        assert result["ok"] is True
        assert result["session_id"] == "mcp-progress"

        message = mailbox.receive(timeout=2.0)
        assert message is not None
        assert message.raw == {
            "type": "progress",
            "task": "API task",
            "pct": 55,
            "msg": "wiring handlers",
        }
    finally:
        mailbox.stop()


def test_report_done_returns_error_without_session_env(monkeypatch):
    monkeypatch.delenv("OPENMAX_SESSION_ID", raising=False)

    result = report_done("API task", "implemented endpoint")

    assert result == {
        "ok": False,
        "error": (
            "session_id is required: pass it as a parameter, or ensure "
            "OPENMAX_SESSION_ID is set in the environment"
        ),
    }


def test_report_progress_soft_fails_without_session_id(monkeypatch):
    """report_progress returns ok with warning instead of hard error when session_id is missing."""
    monkeypatch.delenv("OPENMAX_SESSION_ID", raising=False)

    result = report_progress("API task", 50, "halfway done")

    assert result["ok"] is True
    assert "warning" in result
    assert "no session_id" in result["warning"]


def test_report_progress_rejects_invalid_pct(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-invalid-pct")
    mailbox = SessionMailbox("mcp-invalid-pct", tmp_path)
    mailbox.start()
    try:
        result = report_progress("API task", 101, "still working")
        assert result == {"ok": False, "error": "pct must be an integer between 0 and 100"}
        assert mailbox.receive(timeout=0.2) is None
    finally:
        mailbox.stop()


def test_report_done_returns_error_when_mailbox_is_unavailable(monkeypatch):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-missing-socket")

    result = report_done("API task", "implemented endpoint")

    assert result["ok"] is False
    assert "no active session socket" in result["error"]


def test_report_done_accepts_explicit_session_id(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENMAX_SESSION_ID", raising=False)
    mailbox = SessionMailbox("mcp-explicit", tmp_path)
    mailbox.start()
    try:
        result = report_done("API task", "implemented endpoint", session_id="mcp-explicit")
        assert result["ok"] is True
        assert result["session_id"] == "mcp-explicit"

        message = mailbox.receive(timeout=2.0)
        assert message is not None
        assert message.raw == {
            "type": "done",
            "task": "API task",
            "summary": "implemented endpoint",
        }
    finally:
        mailbox.stop()


def test_explicit_session_id_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-env")
    mailbox = SessionMailbox("mcp-param", tmp_path)
    mailbox.start()
    try:
        result = report_done("API task", "implemented endpoint", session_id="mcp-param")
        assert result["ok"] is True
        assert result["session_id"] == "mcp-param"

        message = mailbox.receive(timeout=2.0)
        assert message is not None
        assert message.raw["task"] == "API task"
    finally:
        mailbox.stop()


def test_execute_with_codex_returns_result(monkeypatch):
    monkeypatch.setattr("openmax.mcp_server.shutil.which", lambda _name: "/usr/bin/codex")

    def fake_run(cmd, *, cwd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="Changes applied", stderr="")

    monkeypatch.setattr("openmax.mcp_server.subprocess.run", fake_run)
    result = execute_with_codex("implement feature", cwd="/tmp")

    assert result["ok"] is True
    assert "Changes applied" in result["output"]
    assert result["exit_code"] == 0


def test_execute_with_codex_empty_task():
    result = execute_with_codex("   ")
    assert result["ok"] is False
    assert result["error"] == "task is required"


def test_execute_with_codex_not_installed(monkeypatch):
    monkeypatch.setattr("openmax.mcp_server.shutil.which", lambda _name: None)
    result = execute_with_codex("do something", cwd="/tmp")
    assert result["ok"] is False
    assert "codex CLI not found" in result["error"]


def test_execute_with_codex_invalid_approval_mode(monkeypatch):
    monkeypatch.setattr("openmax.mcp_server.shutil.which", lambda _name: "/usr/bin/codex")
    result = execute_with_codex("task", approval_mode="yolo")
    assert result["ok"] is False
    assert "invalid approval_mode" in result["error"]


def test_execute_with_codex_timeout(monkeypatch):
    monkeypatch.setattr("openmax.mcp_server.shutil.which", lambda _name: "/usr/bin/codex")

    def fake_run(cmd, *, cwd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr("openmax.mcp_server.subprocess.run", fake_run)
    result = execute_with_codex("slow task", cwd="/tmp", timeout_seconds=10)

    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_report_progress_falls_back_to_env_when_session_id_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAX_SESSION_ID", "mcp-fallback")
    mailbox = SessionMailbox("mcp-fallback", tmp_path)
    mailbox.start()
    try:
        result = report_progress("API task", 55, "wiring handlers", session_id="")
        assert result["ok"] is True
        assert result["session_id"] == "mcp-fallback"

        message = mailbox.receive(timeout=2.0)
        assert message is not None
        assert message.raw["type"] == "progress"
    finally:
        mailbox.stop()
