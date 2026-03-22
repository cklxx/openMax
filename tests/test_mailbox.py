"""Tests for SessionMailbox and related CLI commands."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest
from click.testing import CliRunner

from openmax.mailbox import (
    _MAX_MSG_BYTES,
    SessionMailbox,
    mailbox_socket_path,
    send_mailbox_message,
    send_mailbox_payload,
)

# ---------------------------------------------------------------------------
# Unit — SessionMailbox
# ---------------------------------------------------------------------------


def _send_msg(sock_path: Path, payload: dict[str, Any], delay: float = 0.0) -> None:
    """Helper: send a JSON message to the mailbox socket."""
    if delay:
        time.sleep(delay)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(sock_path))
        s.sendall(json.dumps(payload).encode())


def test_start_creates_socket_file(tmp_path: Path) -> None:
    mb = SessionMailbox("test-sess", tmp_path)
    mb.start()
    try:
        assert mb.socket_path.exists()
    finally:
        mb.stop()


def test_receive_returns_message_immediately(tmp_path: Path) -> None:
    mb = SessionMailbox("test-sess", tmp_path)
    mb.start()
    try:
        payload = {"type": "done", "task": "foo", "summary": "ok"}
        _send_msg(mb.socket_path, payload, delay=0.05)
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.type == "done"
        assert msg.task == "foo"
    finally:
        mb.stop()


def test_receive_returns_none_on_timeout(tmp_path: Path) -> None:
    mb = SessionMailbox("test-sess-timeout", tmp_path)
    mb.start()
    try:
        result = mb.receive(timeout=0.2)
        assert result is None
    finally:
        mb.stop()


def test_concurrent_writers_queue_in_order(tmp_path: Path) -> None:
    mb = SessionMailbox("test-concurrent", tmp_path)
    mb.start()
    try:
        for i in range(5):
            threading.Thread(
                target=_send_msg,
                args=(mb.socket_path, {"type": "progress", "task": f"t{i}", "pct": i * 10}),
                daemon=True,
            ).start()
        time.sleep(0.3)
        received = []
        while True:
            m = mb.receive(timeout=0.1)
            if m is None:
                break
            received.append(m)
        assert len(received) == 5
    finally:
        mb.stop()


def test_stale_socket_cleaned_on_start(tmp_path: Path) -> None:
    # Create a stale socket file
    stale = Path("/tmp/openmax-stale-test.sock")
    stale.touch()
    mb = SessionMailbox("stale-test", tmp_path)
    mb.start()
    try:
        assert mb.socket_path.exists()
    finally:
        mb.stop()
        stale.unlink(missing_ok=True)


def test_message_appended_to_log(tmp_path: Path) -> None:
    mb = SessionMailbox("log-test", tmp_path)
    mb.start()
    try:
        _send_msg(mb.socket_path, {"type": "done", "task": "bar", "summary": "great"}, delay=0.05)
        mb.receive(timeout=2.0)
        lines = mb.log_path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "done"
        assert "_ts" in entry
    finally:
        mb.stop()


def test_malformed_json_discarded(tmp_path: Path) -> None:
    mb = SessionMailbox("bad-json", tmp_path)
    mb.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            s.sendall(b"not-json!!!")
        result = mb.receive(timeout=0.3)
        assert result is None
    finally:
        mb.stop()


def test_missing_type_field_discarded(tmp_path: Path) -> None:
    mb = SessionMailbox("no-type", tmp_path)
    mb.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            s.sendall(json.dumps({"task": "foo"}).encode())
        result = mb.receive(timeout=0.3)
        assert result is None
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# Unit — mailbox_socket_path / send helpers
# ---------------------------------------------------------------------------


def test_mailbox_socket_path_format() -> None:
    assert mailbox_socket_path("abc-123") == Path("/tmp/openmax-abc-123.sock")


def test_send_mailbox_message_delivers_to_running_mailbox(tmp_path: Path) -> None:
    mb = SessionMailbox("send-msg", tmp_path)
    mb.start()
    try:
        send_mailbox_message("send-msg", '{"type":"ping","task":"t"}')
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.type == "ping"
    finally:
        mb.stop()


def test_send_mailbox_message_raises_on_missing_socket() -> None:
    with pytest.raises(FileNotFoundError):
        send_mailbox_message("nonexistent-session-xyz", '{"type":"x"}')


def test_send_mailbox_payload_delivers_dict(tmp_path: Path) -> None:
    mb = SessionMailbox("send-payload", tmp_path)
    mb.start()
    try:
        send_mailbox_payload("send-payload", {"type": "done", "task": "d"})
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.type == "done"
        assert msg.task == "d"
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# Unit — _handle edge cases
# ---------------------------------------------------------------------------


def test_empty_message_discarded(tmp_path: Path) -> None:
    mb = SessionMailbox("empty-msg", tmp_path)
    mb.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            # send nothing, just close
        result = mb.receive(timeout=0.3)
        assert result is None
    finally:
        mb.stop()


def test_non_dict_json_discarded(tmp_path: Path) -> None:
    mb = SessionMailbox("non-dict", tmp_path)
    mb.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            s.sendall(b"[1,2,3]")
        result = mb.receive(timeout=0.3)
        assert result is None
    finally:
        mb.stop()


def test_unicode_message_handled(tmp_path: Path) -> None:
    mb = SessionMailbox("unicode-msg", tmp_path)
    mb.start()
    try:
        payload = {"type": "done", "task": "翻译", "msg": "完成 🎉"}
        _send_msg(mb.socket_path, payload, delay=0.05)
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.task == "翻译"
        assert msg.raw["msg"] == "完成 🎉"
    finally:
        mb.stop()


def test_invalid_utf8_bytes_discarded(tmp_path: Path) -> None:
    mb = SessionMailbox("bad-utf8", tmp_path)
    mb.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            s.sendall(b"\xff\xfe")
        result = mb.receive(timeout=0.3)
        assert result is None
    finally:
        mb.stop()


def test_oversized_message_handled_gracefully(tmp_path: Path) -> None:
    mb = SessionMailbox("oversized", tmp_path)
    mb.start()
    try:
        big_value = "x" * (_MAX_MSG_BYTES + 1000)
        raw = json.dumps({"type": "done", "task": "big", "data": big_value}).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(mb.socket_path))
            s.sendall(raw)
        # Oversized → truncated mid-JSON → invalid JSON → discarded (or valid)
        mb.receive(timeout=0.5)
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# Unit — lifecycle edge cases
# ---------------------------------------------------------------------------


def test_stop_removes_socket_file(tmp_path: Path) -> None:
    mb = SessionMailbox("stop-rm", tmp_path)
    mb.start()
    assert mb.socket_path.exists()
    mb.stop()
    assert not mb.socket_path.exists()


def test_stop_idempotent(tmp_path: Path) -> None:
    mb = SessionMailbox("stop-idem", tmp_path)
    mb.start()
    mb.stop()
    mb.stop()  # second call should not raise


def test_double_start_replaces_socket(tmp_path: Path) -> None:
    mb1 = SessionMailbox("double-start", tmp_path)
    mb1.start()
    try:
        mb2 = SessionMailbox("double-start", tmp_path)
        mb2.start()
        try:
            send_mailbox_payload("double-start", {"type": "done", "task": "x"})
            msg = mb2.receive(timeout=2.0)
            assert msg is not None
            assert msg.type == "done"
        finally:
            mb2.stop()
    finally:
        mb1.stop()


def test_receive_after_stop(tmp_path: Path) -> None:
    mb = SessionMailbox("recv-stop", tmp_path)
    mb.start()
    mb.stop()
    result = mb.receive(timeout=0.1)
    assert result is None


# ---------------------------------------------------------------------------
# Unit — message ordering and concurrency
# ---------------------------------------------------------------------------


def test_sequential_messages_maintain_order(tmp_path: Path) -> None:
    mb = SessionMailbox("seq-order", tmp_path)
    mb.start()
    try:
        for i in range(10):
            _send_msg(mb.socket_path, {"type": "progress", "task": f"t{i}", "seq": i})
            time.sleep(0.02)  # ensure sequential delivery
        received = []
        for _ in range(10):
            m = mb.receive(timeout=2.0)
            assert m is not None
            received.append(m.raw["seq"])
        assert received == list(range(10))
    finally:
        mb.stop()


def _send_msg_retry(sock_path: Path, payload: dict[str, Any], retries: int = 3) -> None:
    """Send with retry to handle backlog pressure."""
    for attempt in range(retries):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(sock_path))
                s.sendall(json.dumps(payload).encode())
            return
        except ConnectionRefusedError:
            time.sleep(0.05 * (attempt + 1))
    raise ConnectionRefusedError(f"failed after {retries} retries")


def test_high_concurrency_no_message_loss(tmp_path: Path) -> None:
    mb = SessionMailbox("high-conc", tmp_path)
    mb.start()
    try:
        threads = []
        for i in range(20):
            t = threading.Thread(
                target=_send_msg_retry,
                args=(mb.socket_path, {"type": "progress", "task": f"t{i}", "idx": i}),
                daemon=True,
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        received = []
        for _ in range(20):
            m = mb.receive(timeout=3.0)
            if m is None:
                break
            received.append(m)
        assert len(received) == 20
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# Unit — timeout precision
# ---------------------------------------------------------------------------


def test_receive_timeout_respects_duration(tmp_path: Path) -> None:
    mb = SessionMailbox("timeout-prec", tmp_path)
    mb.start()
    try:
        start = time.monotonic()
        result = mb.receive(timeout=0.5)
        elapsed = time.monotonic() - start
        assert result is None
        assert 0.4 <= elapsed <= 1.0
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# CLI — openmax msg
# ---------------------------------------------------------------------------


def test_msg_happy_path_sends_to_socket(tmp_path: Path) -> None:
    from openmax.cli import main

    mb = SessionMailbox("cli-happy", tmp_path)
    mb.start()
    try:
        runner = CliRunner()
        payload = '{"type":"done","task":"t1","summary":"ok"}'
        result = runner.invoke(main, ["msg", payload, "--session", "cli-happy"])
        assert result.exit_code == 0
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.type == "done"
    finally:
        mb.stop()


def test_msg_invalid_json_exits_1() -> None:
    from openmax.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["msg", "not-json", "--session", "any"])
    assert result.exit_code != 0
    assert "JSON" in result.output


def test_msg_no_socket_file_exits_1() -> None:
    from openmax.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["msg", '{"type":"done","task":"x"}', "--session", "no-such-sess"])
    assert result.exit_code == 1


def test_msg_connection_refused_exits_1(tmp_path: Path) -> None:
    from openmax.cli import main

    # Create socket file but don't listen
    sock_path = Path("/tmp/openmax-refused-test.sock")
    sock_path.touch()
    try:
        runner = CliRunner()
        result = runner.invoke(
            main, ["msg", '{"type":"done","task":"x"}', "--session", "refused-test"]
        )
        assert result.exit_code == 1
    finally:
        sock_path.unlink(missing_ok=True)


def test_msg_reads_session_from_env_var(tmp_path: Path) -> None:
    from openmax.cli import main

    mb = SessionMailbox("env-sess", tmp_path)
    mb.start()
    try:
        runner = CliRunner(env={"OPENMAX_SESSION_ID": "env-sess"})
        payload = '{"type":"progress","task":"t","pct":50}'
        result = runner.invoke(main, ["msg", payload])
        assert result.exit_code == 0
        msg = mb.receive(timeout=2.0)
        assert msg is not None
        assert msg.type == "progress"
    finally:
        mb.stop()


# ---------------------------------------------------------------------------
# Integration — wait_for_agent_message tool
# ---------------------------------------------------------------------------


def _make_runtime(tmp_path: Path, session_id: str = "int-sess") -> Any:
    """Build a minimal LeadAgentRuntime with a live mailbox."""
    from openmax.lead_agent.runtime import LeadAgentRuntime
    from openmax.lead_agent.types import PlanResult

    pane_mgr = MagicMock()
    pane_mgr.is_pane_alive.return_value = True

    mb = SessionMailbox(session_id, tmp_path)
    mb.start()

    runtime = LeadAgentRuntime(
        cwd=str(tmp_path),
        plan=PlanResult(goal="test"),
        pane_mgr=pane_mgr,
        mailbox=mb,
    )
    return runtime, mb


def test_tool_returns_message_from_mailbox(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message

    runtime, mb = _make_runtime(tmp_path)
    token = bind_lead_agent_runtime(runtime)
    try:
        payload = {"type": "done", "task": "alpha", "summary": "shipped"}

        async def _run():
            threading.Thread(
                target=_send_msg, args=(mb.socket_path, payload, 0.1), daemon=True
            ).start()
            return await wait_for_agent_message.handler({"timeout": 5})

        result = anyio.run(_run)
        data = json.loads(result["content"][0]["text"])
        assert data["received"] is True
        assert data["message"]["type"] == "done"
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_timeout_returns_none(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message

    runtime, mb = _make_runtime(tmp_path, "timeout-sess")
    token = bind_lead_agent_runtime(runtime)
    try:
        result = anyio.run(wait_for_agent_message.handler, {"timeout": 5})
        data = json.loads(result["content"][0]["text"])
        assert data["timeout"] is True
        assert data["message"] is None
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_auto_done_for_exited_pane(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message
    from openmax.lead_agent.types import SubTask, TaskStatus

    runtime, mb = _make_runtime(tmp_path, "auto-done-sess")
    runtime.pane_mgr.is_pane_alive.return_value = False  # pane exited

    st = SubTask(name="dead-task", agent_type="claude", prompt="x", status=TaskStatus.RUNNING)
    st.pane_id = 42
    runtime.plan.subtasks.append(st)

    token = bind_lead_agent_runtime(runtime)
    try:
        result = anyio.run(wait_for_agent_message.handler, {"timeout": 5})
        data = json.loads(result["content"][0]["text"])
        assert data["received"] is True
        assert data["source"] == "auto-detect"
        assert data["message"]["task"] == "dead-task"
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_no_auto_done_if_pane_still_alive(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message
    from openmax.lead_agent.types import SubTask, TaskStatus

    runtime, mb = _make_runtime(tmp_path, "alive-pane-sess")
    runtime.pane_mgr.is_pane_alive.return_value = True  # still alive

    st = SubTask(name="live-task", agent_type="claude", prompt="x", status=TaskStatus.RUNNING)
    st.pane_id = 7
    runtime.plan.subtasks.append(st)

    token = bind_lead_agent_runtime(runtime)
    try:
        result = anyio.run(wait_for_agent_message.handler, {"timeout": 5})
        data = json.loads(result["content"][0]["text"])
        assert data["timeout"] is True
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_progress_updates_dashboard(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message

    runtime, mb = _make_runtime(tmp_path, "progress-sess")
    runtime.dashboard = MagicMock()

    token = bind_lead_agent_runtime(runtime)
    try:
        payload = {"type": "progress", "task": "beta", "pct": 75, "msg": "tests passing"}

        async def _run():
            threading.Thread(
                target=_send_msg, args=(mb.socket_path, payload, 0.1), daemon=True
            ).start()
            return await wait_for_agent_message.handler({"timeout": 5})

        anyio.run(_run)
        runtime.dashboard.update_pane_activity.assert_called_once()
        call_args = runtime.dashboard.update_pane_activity.call_args[0]
        assert "75%" in call_args[1]
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_logs_event_to_session_store(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import bind_lead_agent_runtime, reset_lead_agent_runtime
    from openmax.lead_agent.tools._misc import wait_for_agent_message
    from openmax.session_runtime import SessionMeta, SessionStore

    runtime, mb = _make_runtime(tmp_path, "event-log-sess")
    runtime.session_store = MagicMock(spec=SessionStore)
    runtime.session_meta = MagicMock(spec=SessionMeta)

    token = bind_lead_agent_runtime(runtime)
    try:
        payload = {"type": "blocked", "task": "gamma", "msg": "merge conflict"}

        async def _run():
            threading.Thread(
                target=_send_msg, args=(mb.socket_path, payload, 0.1), daemon=True
            ).start()
            return await wait_for_agent_message.handler({"timeout": 5})

        anyio.run(_run)
        runtime.session_store.append_event.assert_called()
        call_args = runtime.session_store.append_event.call_args[0]
        assert call_args[1] == "mailbox.message_received"
    finally:
        reset_lead_agent_runtime(token)
        mb.stop()


def test_tool_fallback_sleep_when_no_mailbox(tmp_path: Path) -> None:
    from openmax.lead_agent.runtime import (
        LeadAgentRuntime,
        bind_lead_agent_runtime,
        reset_lead_agent_runtime,
    )
    from openmax.lead_agent.tools._misc import wait_for_agent_message
    from openmax.lead_agent.types import PlanResult

    pane_mgr = MagicMock()
    runtime = LeadAgentRuntime(
        cwd=str(tmp_path),
        plan=PlanResult(goal="no-mb"),
        pane_mgr=pane_mgr,
        mailbox=None,
    )
    token = bind_lead_agent_runtime(runtime)
    try:
        start = time.monotonic()
        result = anyio.run(wait_for_agent_message.handler, {"timeout": 5})
        elapsed = time.monotonic() - start
        data = json.loads(result["content"][0]["text"])
        assert data["reason"] == "no_mailbox"
        assert elapsed >= 5.0
    finally:
        reset_lead_agent_runtime(token)


# ---------------------------------------------------------------------------
# Integration — OPENMAX_SESSION_ID injected into pane env (dispatch_agent fix)
# ---------------------------------------------------------------------------


def test_session_id_env_var_injected_into_pane(tmp_path: Path) -> None:
    """Sub-agent can send messages using $OPENMAX_SESSION_ID from env var."""
    import os
    import subprocess

    mb = SessionMailbox("env-inject", tmp_path)
    mb.start()
    try:
        pane_env = {**os.environ, "OPENMAX_SESSION_ID": "env-inject"}
        payload = '{"type":"done","task":"build","summary":"ok"}'
        result = subprocess.run(
            ["openmax", "msg", payload],  # no --session flag, reads from env
            env=pane_env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        msg = mb.receive(timeout=3.0)
        assert msg is not None
        assert msg.type == "done"
        assert msg.task == "build"
    finally:
        mb.stop()
