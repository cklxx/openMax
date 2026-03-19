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
from click.testing import CliRunner

from openmax.mailbox import SessionMailbox

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
