from __future__ import annotations

from openmax.mailbox import SessionMailbox
from openmax.mcp_server import report_done, report_progress


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

    assert result == {"ok": False, "error": "OPENMAX_SESSION_ID is not set"}


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
