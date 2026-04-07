"""Integration tests for TmuxPaneBackend — runs against a real tmux server."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import uuid

import pytest

from openmax.pane_backend import PaneBackendError, TmuxPaneBackend, _tmux_id

# Skip entire module if tmux is not installed
pytestmark = pytest.mark.skipif(
    not shutil.which("tmux"),
    reason="tmux not installed",
)

_SESSION = "openmax_test"


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


@pytest.fixture()
def tmux_backend():
    """Create an isolated tmux server + session, yield a backend, then tear down."""
    socket = f"openmax_test_{uuid.uuid4().hex[:8]}"

    # Start a detached tmux session on a dedicated socket
    subprocess.run(
        ["tmux", "-L", socket, "new-session", "-d", "-s", _SESSION, "-x", "120", "-y", "40"],
        check=True,
        capture_output=True,
        timeout=10,
    )

    backend = TmuxPaneBackend(socket_name=socket, target_session=_SESSION)

    yield backend

    # Tear down: kill the entire tmux server
    subprocess.run(
        ["tmux", "-L", socket, "kill-server"],
        capture_output=True,
        timeout=5,
    )


def _long_running_cmd() -> list[str]:
    """A command that stays alive, prints 'ready', echoes one stdin line."""
    return [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys, time; "
            "print('ready', flush=True); "
            "line = sys.stdin.readline().strip(); "
            "print(f'ECHO:{line}', flush=True); "
            "time.sleep(60)"
        ),
    ]


def _quick_print_cmd(text: str) -> list[str]:
    """A command that prints text and sleeps."""
    return [sys.executable, "-u", "-c", f"import time; print({text!r}, flush=True); time.sleep(60)"]


# ── list_panes ──────────────────────────────────────────────────────


def test_list_panes_returns_initial_session_pane(tmux_backend: TmuxPaneBackend):
    panes = tmux_backend.list_panes()
    # The session was created with one default pane
    assert len(panes) >= 1
    pane = panes[0]
    assert pane.workspace == "tmux"
    assert pane.rows > 0
    assert pane.cols > 0


# ── spawn_window ────────────────────────────────────────────────────


def test_spawn_window_creates_pane_and_runs_command(tmux_backend: TmuxPaneBackend):
    panes_before = len(tmux_backend.list_panes())
    cmd = _quick_print_cmd("hello_spawn")
    pane_id = tmux_backend.spawn_window(cmd)

    assert isinstance(pane_id, int)
    _wait_until(lambda: len(tmux_backend.list_panes()) > panes_before)

    _wait_until(lambda: "hello_spawn" in tmux_backend.get_text(pane_id))


def test_spawn_window_with_cwd(tmux_backend: TmuxPaneBackend):
    script = "import os, time; print(os.getcwd(), flush=True); time.sleep(60)"
    cmd = [sys.executable, "-u", "-c", script]
    pane_id = tmux_backend.spawn_window(cmd, cwd="/tmp")

    _wait_until(lambda: "/tmp" in tmux_backend.get_text(pane_id))


def test_spawn_window_with_env(tmux_backend: TmuxPaneBackend):
    secret = "test_secret_value_42"
    cmd = [
        sys.executable,
        "-u",
        "-c",
        (
            "import os, time; "
            "print(os.environ.get('MY_TEST_VAR', 'missing'), flush=True); "
            "time.sleep(60)"
        ),
    ]
    pane_id = tmux_backend.spawn_window(cmd, env={"MY_TEST_VAR": secret})

    _wait_until(lambda: secret in tmux_backend.get_text(pane_id))
    # Verify env is passed out-of-band, not in command args
    assert secret not in " ".join(cmd)


# ── split_pane ──────────────────────────────────────────────────────


def test_split_pane_right(tmux_backend: TmuxPaneBackend):
    parent_id = tmux_backend.spawn_window(_quick_print_cmd("parent"))
    _wait_until(lambda: "parent" in tmux_backend.get_text(parent_id))

    child_id = tmux_backend.split_pane(parent_id, "right", _quick_print_cmd("child_right"))
    assert child_id != parent_id

    _wait_until(lambda: "child_right" in tmux_backend.get_text(child_id))
    # Both panes should be visible
    pane_ids = {p.pane_id for p in tmux_backend.list_panes()}
    assert parent_id in pane_ids
    assert child_id in pane_ids


def test_split_pane_bottom(tmux_backend: TmuxPaneBackend):
    parent_id = tmux_backend.spawn_window(_quick_print_cmd("top"))
    child_id = tmux_backend.split_pane(parent_id, "bottom", _quick_print_cmd("bottom"))

    _wait_until(lambda: "bottom" in tmux_backend.get_text(child_id))


def test_split_pane_left(tmux_backend: TmuxPaneBackend):
    parent_id = tmux_backend.spawn_window(_quick_print_cmd("right_pane"))
    child_id = tmux_backend.split_pane(parent_id, "left", _quick_print_cmd("left_pane"))

    _wait_until(lambda: "left_pane" in tmux_backend.get_text(child_id))


def test_split_pane_top(tmux_backend: TmuxPaneBackend):
    parent_id = tmux_backend.spawn_window(_quick_print_cmd("lower"))
    child_id = tmux_backend.split_pane(parent_id, "top", _quick_print_cmd("upper"))

    _wait_until(lambda: "upper" in tmux_backend.get_text(child_id))


def test_split_pane_with_cwd(tmux_backend: TmuxPaneBackend):
    parent_id = tmux_backend.spawn_window(_quick_print_cmd("p"))
    cmd = [
        sys.executable,
        "-u",
        "-c",
        "import os; print(os.getcwd(), flush=True); import time; time.sleep(60)",
    ]
    child_id = tmux_backend.split_pane(parent_id, "right", cmd, cwd="/tmp")

    _wait_until(lambda: "/tmp" in tmux_backend.get_text(child_id))


# ── send_text + send_enter ──────────────────────────────────────────


def test_send_text_and_enter(tmux_backend: TmuxPaneBackend):
    cmd = _long_running_cmd()
    pane_id = tmux_backend.spawn_window(cmd)

    _wait_until(lambda: "ready" in tmux_backend.get_text(pane_id))

    tmux_backend.send_text(pane_id, "hello_world")
    tmux_backend.send_enter(pane_id)

    _wait_until(lambda: "ECHO:hello_world" in tmux_backend.get_text(pane_id))


def test_send_text_multiline(tmux_backend: TmuxPaneBackend):
    """Verify that multi-line text is sent literally (no interpretation)."""
    cmd = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys; "
            "lines = []; "
            "[lines.append(sys.stdin.readline().strip()) for _ in range(2)]; "
            "print(f'GOT:{\"|\".join(lines)}', flush=True); "
            "import time; time.sleep(60)"
        ),
    ]
    pane_id = tmux_backend.spawn_window(cmd)
    time.sleep(0.5)

    # Send two lines separately
    tmux_backend.send_text(pane_id, "line_one")
    tmux_backend.send_enter(pane_id)
    tmux_backend.send_text(pane_id, "line_two")
    tmux_backend.send_enter(pane_id)

    _wait_until(lambda: "GOT:line_one|line_two" in tmux_backend.get_text(pane_id))


# ── get_text ────────────────────────────────────────────────────────


def test_get_text_returns_pane_content(tmux_backend: TmuxPaneBackend):
    pane_id = tmux_backend.spawn_window(_quick_print_cmd("content_test"))

    _wait_until(lambda: "content_test" in tmux_backend.get_text(pane_id))

    text = tmux_backend.get_text(pane_id)
    assert "content_test" in text


def test_get_text_with_start_line(tmux_backend: TmuxPaneBackend):
    cmd = [
        sys.executable,
        "-u",
        "-c",
        "for i in range(5): print(f'LINE{i}', flush=True)\nimport time; time.sleep(60)",
    ]
    pane_id = tmux_backend.spawn_window(cmd)

    _wait_until(lambda: "LINE4" in tmux_backend.get_text(pane_id))

    full_text = tmux_backend.get_text(pane_id)
    full_lines = full_text.splitlines()
    # Find which line LINE0 is on
    line0_idx = next(i for i, ln in enumerate(full_lines) if "LINE0" in ln)

    partial_text = tmux_backend.get_text(pane_id, start_line=line0_idx + 2)
    # Should not contain LINE0 or LINE1
    assert "LINE0" not in partial_text
    assert "LINE2" in partial_text


def test_get_text_for_dead_pane_returns_empty(tmux_backend: TmuxPaneBackend):
    pane_id = tmux_backend.spawn_window(_quick_print_cmd("soon_dead"))
    _wait_until(lambda: "soon_dead" in tmux_backend.get_text(pane_id))

    tmux_backend.kill_pane(pane_id)
    time.sleep(0.3)

    # After kill, get_text should return empty (check=False in implementation)
    text = tmux_backend.get_text(pane_id)
    assert text == ""


# ── activate_pane ───────────────────────────────────────────────────


def test_activate_pane(tmux_backend: TmuxPaneBackend):
    pane1 = tmux_backend.spawn_window(_quick_print_cmd("pane1"))
    pane2 = tmux_backend.split_pane(pane1, "right", _quick_print_cmd("pane2"))

    tmux_backend.activate_pane(pane1)
    panes = tmux_backend.list_panes()
    # Find the window these panes belong to
    window_panes = [p for p in panes if p.pane_id in (pane1, pane2)]
    active = [p for p in window_panes if p.is_active]
    assert len(active) == 1
    assert active[0].pane_id == pane1

    tmux_backend.activate_pane(pane2)
    panes = tmux_backend.list_panes()
    window_panes = [p for p in panes if p.pane_id in (pane1, pane2)]
    active = [p for p in window_panes if p.is_active]
    assert len(active) == 1
    assert active[0].pane_id == pane2


def test_activate_pane_dead_pane_does_not_raise(tmux_backend: TmuxPaneBackend):
    """activate_pane with check=False should not raise for unknown pane."""
    tmux_backend.activate_pane(99999)  # non-existent pane — should not raise


# ── set_window_title ────────────────────────────────────────────────


def test_set_window_title(tmux_backend: TmuxPaneBackend):
    pane_id = tmux_backend.spawn_window(_quick_print_cmd("titled"))
    _wait_until(lambda: "titled" in tmux_backend.get_text(pane_id))

    tmux_backend.set_window_title(pane_id, "MyAgentWindow")

    # tmux window title is reflected in the window name — check via raw tmux command
    result = tmux_backend._run_tmux(
        ["list-windows", "-F", "#{window_name}"],
        check=False,
    )
    assert "MyAgentWindow" in result.stdout


def test_set_window_title_dead_pane_does_not_raise(tmux_backend: TmuxPaneBackend):
    tmux_backend.set_window_title(99999, "noop")  # should not raise


# ── kill_pane ───────────────────────────────────────────────────────


def test_kill_pane_removes_pane(tmux_backend: TmuxPaneBackend):
    pane_id = tmux_backend.spawn_window(_quick_print_cmd("killme"))
    _wait_until(lambda: "killme" in tmux_backend.get_text(pane_id))

    pane_ids_before = {p.pane_id for p in tmux_backend.list_panes()}
    assert pane_id in pane_ids_before

    tmux_backend.kill_pane(pane_id)
    time.sleep(0.3)

    pane_ids_after = {p.pane_id for p in tmux_backend.list_panes()}
    assert pane_id not in pane_ids_after


def test_kill_pane_dead_pane_does_not_raise(tmux_backend: TmuxPaneBackend):
    tmux_backend.kill_pane(99999)  # should not raise


# ── resize_frontmost_window ─────────────────────────────────────────


def test_resize_frontmost_window_is_noop(tmux_backend: TmuxPaneBackend):
    assert tmux_backend.resize_frontmost_window() is None


# ── _run_tmux error handling ────────────────────────────────────────


def test_run_tmux_raises_on_failure(tmux_backend: TmuxPaneBackend):
    with pytest.raises(PaneBackendError, match="tmux"):
        tmux_backend._run_tmux(["this-is-not-a-real-command"], check=True)


# ── _tmux_id helper ─────────────────────────────────────────────────


def test_tmux_id_strips_prefix():
    assert _tmux_id("%42") == 42
    assert _tmux_id("@7") == 7
    assert _tmux_id("$0") == 0
    assert _tmux_id("123") == 123


# ── CLAUDECODE env is unset ─────────────────────────────────────────


def test_spawned_pane_does_not_inherit_claudecode_env(tmux_backend: TmuxPaneBackend):
    cmd = [
        sys.executable,
        "-u",
        "-c",
        (
            "import os; "
            "val = os.environ.get('CLAUDECODE', 'CLEAN'); "
            "print(f'CLAUDECODE={val}', flush=True); "
            "import time; time.sleep(60)"
        ),
    ]
    # Even if CLAUDECODE is set in our env, the spawned pane should not see it
    pane_id = tmux_backend.spawn_window(cmd)
    _wait_until(lambda: "CLAUDECODE=" in tmux_backend.get_text(pane_id))

    text = tmux_backend.get_text(pane_id)
    assert "CLAUDECODE=CLEAN" in text


# ── session recovery ───────────────────────────────────────────────


def test_spawn_window_recreates_dead_session(tmux_backend: TmuxPaneBackend):
    """spawn_window re-creates the target session if it was externally killed."""
    if not tmux_backend._target_session:
        pytest.skip("test requires a named target session")

    # Kill the session externally
    tmux_backend._run_tmux(["kill-session", "-t", tmux_backend._target_session], check=False)
    time.sleep(0.2)

    # spawn_window should recover and succeed
    pane_id = tmux_backend.spawn_window(_quick_print_cmd("recovered"))
    _wait_until(lambda: "recovered" in tmux_backend.get_text(pane_id))


# ── Full lifecycle: spawn → split → send → read → kill ──────────────


def test_full_lifecycle(tmux_backend: TmuxPaneBackend):
    """End-to-end: spawn window, split, send text, read output, kill."""
    # 1. Spawn window
    pane1 = tmux_backend.spawn_window(_long_running_cmd(), cwd="/tmp")
    _wait_until(lambda: "ready" in tmux_backend.get_text(pane1))

    # 2. Split right
    pane2 = tmux_backend.split_pane(pane1, "right", _long_running_cmd())
    _wait_until(lambda: "ready" in tmux_backend.get_text(pane2))

    # 3. Both panes listed
    pane_ids = {p.pane_id for p in tmux_backend.list_panes()}
    assert pane1 in pane_ids
    assert pane2 in pane_ids

    # 4. Send text to pane1
    tmux_backend.send_text(pane1, "msg_for_pane1")
    tmux_backend.send_enter(pane1)
    _wait_until(lambda: "ECHO:msg_for_pane1" in tmux_backend.get_text(pane1))

    # 5. Send text to pane2
    tmux_backend.send_text(pane2, "msg_for_pane2")
    tmux_backend.send_enter(pane2)
    _wait_until(lambda: "ECHO:msg_for_pane2" in tmux_backend.get_text(pane2))

    # 6. Activate pane1
    tmux_backend.activate_pane(pane1)

    # 7. Set title
    tmux_backend.set_window_title(pane1, "lifecycle_test")

    # 8. Kill pane2
    tmux_backend.kill_pane(pane2)
    time.sleep(0.3)
    pane_ids = {p.pane_id for p in tmux_backend.list_panes()}
    assert pane2 not in pane_ids
    assert pane1 in pane_ids

    # 9. Kill pane1
    tmux_backend.kill_pane(pane1)
    time.sleep(0.3)
