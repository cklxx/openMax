from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from openmax.pane_backend import (
    GhosttyPaneBackend,
    HeadlessPaneBackend,
    KakuPaneBackend,
    PaneBackendError,
    TmuxPaneBackend,
    create_pane_backend,
    resolve_pane_backend_name,
)


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _echo_worker_command() -> list[str]:
    return [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys, time; "
            "print('ready', flush=True); "
            "line = sys.stdin.readline().strip(); "
            "print(f'ECHO:{line}', flush=True); "
            "time.sleep(30)"
        ),
    ]


def test_headless_backend_supports_spawn_send_read_and_kill():
    backend = HeadlessPaneBackend()

    pane_id = backend.spawn_window(_echo_worker_command(), cwd="/tmp")

    _wait_until(lambda: "ready" in backend.get_text(pane_id))
    panes = backend.list_panes()
    assert len(panes) == 1
    assert panes[0].pane_id == pane_id
    assert panes[0].window_id >= 1

    backend.send_text(pane_id, "ping")
    backend.send_enter(pane_id)

    _wait_until(lambda: "ECHO:ping" in backend.get_text(pane_id))

    backend.kill_pane(pane_id)
    _wait_until(lambda: backend.list_panes() == [])


def test_headless_backend_spawn_failure_raises_stable_error():
    backend = HeadlessPaneBackend()

    with pytest.raises(PaneBackendError, match="headless pane spawn failed"):
        backend.spawn_window(["/definitely-missing-openmax-command"])


def test_headless_backend_injects_agent_env_without_putting_secret_in_command():
    backend = HeadlessPaneBackend()
    secret = "sk-kimi-rJBeBAhtWvMxhHtUFWZ5eva8QvUsAt0ZoIVWAHM8Th197GNKKiNgGsAneYmkDbZy"
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import os, time; "
            "print(os.environ.get('OPENAI_API_KEY', 'missing'), flush=True); "
            "time.sleep(30)"
        ),
    ]

    pane_id = backend.spawn_window(command, cwd="/tmp", env={"OPENAI_API_KEY": secret})

    _wait_until(lambda: secret in backend.get_text(pane_id))
    assert secret not in " ".join(command)

    backend.kill_pane(pane_id)


def test_headless_backend_get_text_for_unknown_pane_raises_stable_error():
    backend = HeadlessPaneBackend()

    with pytest.raises(PaneBackendError, match="unknown pane: 999"):
        backend.get_text(999)


def test_resolve_pane_backend_name_auto_detects_when_no_env(monkeypatch):
    monkeypatch.delenv("OPENMAX_PANE_BACKEND", raising=False)
    monkeypatch.delenv("TMUX", raising=False)

    # Auto-detect picks the first available backend (kaku > ghostty > tmux)
    resolved = resolve_pane_backend_name()
    assert resolved in ("kaku", "ghostty", "tmux")


def test_resolve_pane_backend_name_uses_env_and_normalizes_case(monkeypatch):
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "HEADLESS")

    assert resolve_pane_backend_name() == "headless"


def test_resolve_pane_backend_name_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "broken")

    with pytest.raises(ValueError, match="Unknown pane backend: broken"):
        resolve_pane_backend_name()


def test_create_pane_backend_builds_requested_backend(monkeypatch):
    assert isinstance(create_pane_backend("headless"), HeadlessPaneBackend)
    assert isinstance(create_pane_backend("kaku"), KakuPaneBackend)
    # Tmux backend calls `tmux` at init time; skip when tmux is not installed.
    import shutil

    if shutil.which("tmux") is None:
        pytest.skip("tmux not available")
    assert isinstance(create_pane_backend("tmux"), TmuxPaneBackend)


def test_kaku_backend_passes_agent_env_out_of_band(monkeypatch):
    backend = KakuPaneBackend()
    calls: list[tuple] = []
    secret = "sk-kimi-rJBeBAhtWvMxhHtUFWZ5eva8QvUsAt0ZoIVWAHM8Th197GNKKiNgGsAneYmkDbZy"

    def fake_run_kaku(args, **kwargs):
        calls.append((args, kwargs))
        return type("Result", (), {"stdout": "11\n"})()

    monkeypatch.setattr(backend, "_run_kaku", fake_run_kaku)

    pane_id = backend.spawn_window(
        ["codex", "exec"],
        cwd="/repo",
        env={"OPENAI_API_KEY": secret, "OPENAI_BASE_URL": "https://api.moonshot.cn/v1"},
    )

    assert pane_id == 11
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == [
        "spawn",
        "--new-window",
        "--cwd",
        "/repo",
        "--",
        "env",
        "-u",
        "CLAUDECODE",
        "-u",
        "CLAUDE_CODE_ENTRYPOINT",
        "codex",
        "exec",
    ]
    assert kwargs["env"]["OPENAI_API_KEY"] == secret
    assert kwargs["env"]["OPENAI_BASE_URL"] == "https://api.moonshot.cn/v1"
    assert secret not in " ".join(args)


# ── KakuPaneBackend retry ─────────────────────────────────────────────────────


def test_kaku_spawn_window_retries_on_transient_failure(monkeypatch):
    backend = KakuPaneBackend()
    attempt = 0

    def flaky_run_kaku(args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise PaneBackendError("transient kaku timeout")
        return type("R", (), {"stdout": "42\n"})()

    monkeypatch.setattr(backend, "_run_kaku", flaky_run_kaku)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    pane_id = backend.spawn_window(["echo", "hi"])

    assert pane_id == 42
    assert attempt == 3  # failed twice, succeeded on third


def test_kaku_spawn_window_raises_after_max_retries(monkeypatch):
    backend = KakuPaneBackend()

    def always_fail(args, **kwargs):
        raise PaneBackendError("kaku not responding")

    monkeypatch.setattr(backend, "_run_kaku", always_fail)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    with pytest.raises(PaneBackendError, match="kaku not responding"):
        backend.spawn_window(["echo", "hi"])


def test_kaku_split_pane_retries_on_transient_failure(monkeypatch):
    backend = KakuPaneBackend()
    attempt = 0

    def flaky_run_kaku(args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt < 2:
            raise PaneBackendError("transient split failure")
        return type("R", (), {"stdout": "99\n"})()

    monkeypatch.setattr(backend, "_run_kaku", flaky_run_kaku)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    pane_id = backend.split_pane(10, "right", ["echo", "hi"])

    assert pane_id == 99
    assert attempt == 2


def test_kaku_retry_does_not_sleep_on_first_success(monkeypatch):
    backend = KakuPaneBackend()
    sleep_calls: list[float] = []

    monkeypatch.setattr(backend, "_run_kaku", lambda args, **kw: type("R", (), {"stdout": "7\n"})())
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda s: sleep_calls.append(s))

    backend.spawn_window(["echo"])

    assert sleep_calls == []  # no sleep when first attempt succeeds


# ── GhosttyPaneBackend ────────────────────────────────────────────────────────


def test_ghostty_list_panes_parses_output(monkeypatch):
    backend = GhosttyPaneBackend()
    tsv = "1\t10\t100\tmy-shell\t/home/user\t120\t40\n2\t20\t200\tzsh\t/tmp\t80\t24\n"
    fake_result = type("R", (), {"returncode": 0, "stdout": tsv, "stderr": ""})()
    monkeypatch.setattr(backend, "_run_applescript", lambda *a, **kw: fake_result)

    panes = backend.list_panes()

    assert len(panes) == 2
    assert panes[0].pane_id == 100
    assert panes[0].window_id == 1
    assert panes[0].title == "my-shell"
    assert panes[0].cwd == "/home/user"
    assert panes[0].cols == 120
    assert panes[0].rows == 40
    assert panes[1].pane_id == 200


def test_ghostty_spawn_window_builds_script(monkeypatch):
    backend = GhosttyPaneBackend()
    scripts: list[str] = []

    def fake_run(script, **kwargs):
        scripts.append(script)
        return type("R", (), {"stdout": "42\n", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(backend, "_run_applescript", fake_run)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    pane_id = backend.spawn_window(["echo", "hello"], cwd="/repo")

    assert pane_id == 42
    assert len(scripts) == 1
    assert "new window with configuration" in scripts[0]
    assert "working directory" in scripts[0]
    assert "/repo" in scripts[0]


def test_ghostty_split_direction_mapping(monkeypatch):
    backend = GhosttyPaneBackend()
    scripts: list[str] = []

    def fake_run(script, **kwargs):
        scripts.append(script)
        return type("R", (), {"stdout": "99\n", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(backend, "_run_applescript", fake_run)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    backend.split_pane(10, "bottom", ["echo"])
    assert "direction down" in scripts[0]

    scripts.clear()
    backend.split_pane(10, "top", ["echo"])
    assert "direction up" in scripts[0]

    scripts.clear()
    backend.split_pane(10, "right", ["echo"])
    assert "direction right" in scripts[0]

    scripts.clear()
    backend.split_pane(10, "left", ["echo"])
    assert "direction left" in scripts[0]


def test_ghostty_send_text_escapes_quotes(monkeypatch):
    backend = GhosttyPaneBackend()
    scripts: list[str] = []

    def fake_run(script, **kwargs):
        scripts.append(script)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(backend, "_run_applescript", fake_run)

    backend.send_text(5, 'echo "hello world"')

    assert len(scripts) == 1
    assert r"\"hello world\"" in scripts[0]
    assert "terminal id 5" in scripts[0]


def test_ghostty_get_text_clipboard_cycle(monkeypatch):
    backend = GhosttyPaneBackend()
    clipboard_state = {"value": "original-clipboard"}
    scripts: list[str] = []

    def fake_run(script, **kwargs):
        scripts.append(script)
        if "write_scrollback_file" in script:
            clipboard_state["value"] = "line1\nline2\nline3"
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    def fake_pbpaste(*args, **kwargs):
        return type("R", (), {"stdout": clipboard_state["value"], "returncode": 0})()

    pbcopy_inputs: list[str] = []

    def fake_pbcopy(*args, **kwargs):
        pbcopy_inputs.append(kwargs.get("input", ""))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(backend, "_run_applescript", fake_run)
    monkeypatch.setattr(
        "openmax.pane_backend.subprocess.run",
        lambda cmd, **kw: (
            fake_pbpaste(cmd, **kw)
            if cmd == ["pbpaste"]
            else fake_pbcopy(cmd, **kw)
            if cmd == ["pbcopy"]
            else type("R", (), {"stdout": "", "returncode": 0})()
        ),
    )
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    text = backend.get_text(7)

    assert text == "line1\nline2\nline3"
    assert pbcopy_inputs[-1] == "original-clipboard"


def test_ghostty_get_text_with_start_line(monkeypatch):
    backend = GhosttyPaneBackend()

    def fake_run(script, **kwargs):
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    def fake_subprocess_run(cmd, **kwargs):
        if cmd == ["pbpaste"]:
            return type("R", (), {"stdout": "line0\nline1\nline2\nline3", "returncode": 0})()
        return type("R", (), {"stdout": "", "returncode": 0})()

    monkeypatch.setattr(backend, "_run_applescript", fake_run)
    monkeypatch.setattr("openmax.pane_backend.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("openmax.pane_backend.time.sleep", lambda _: None)

    text = backend.get_text(7, start_line=2)

    assert text == "line2\nline3"


def test_resolve_backend_accepts_ghostty(monkeypatch):
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "ghostty")

    assert resolve_pane_backend_name() == "ghostty"


def test_create_backend_builds_ghostty():
    assert isinstance(create_pane_backend("ghostty"), GhosttyPaneBackend)


# --- KakuPaneBackend._heal_socket_symlink tests ---


@pytest.fixture()
def _short_tmp(tmp_path):
    """Yield a short-path directory suitable for AF_UNIX sockets (macOS 104-byte limit)."""
    import tempfile

    d = tempfile.mkdtemp(prefix="om_")
    yield Path(d)
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _make_unix_socket(path: Path) -> None:
    """Create a real Unix domain socket file for testing."""
    import socket as sock_mod

    s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
    s.bind(str(path))
    s.close()


class TestHealSocketSymlink:
    """Verify stale Kaku socket symlink is auto-repaired."""

    def setup_method(self):
        KakuPaneBackend._socket_healed = False

    def test_fixes_stale_symlink(self, _short_tmp, monkeypatch):
        live = _short_tmp / "gui-sock-999"
        _make_unix_socket(live)
        stale = _short_tmp / "gui-sock-111"
        stale.touch()
        default = _short_tmp / "default-fun.tw93.kaku"
        default.symlink_to(stale)

        monkeypatch.setenv("KAKU_UNIX_SOCKET", str(live))
        KakuPaneBackend._heal_socket_symlink()

        assert default.resolve() == live.resolve()

    def test_noop_when_symlink_correct(self, _short_tmp, monkeypatch):
        live = _short_tmp / "gui-sock-999"
        _make_unix_socket(live)
        default = _short_tmp / "default-fun.tw93.kaku"
        default.symlink_to(live)

        monkeypatch.setenv("KAKU_UNIX_SOCKET", str(live))
        KakuPaneBackend._heal_socket_symlink()

        assert default.resolve() == live.resolve()

    def test_noop_when_no_env_var(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KAKU_UNIX_SOCKET", raising=False)
        default = tmp_path / "default-fun.tw93.kaku"
        default.symlink_to(tmp_path / "gui-sock-old")

        KakuPaneBackend._heal_socket_symlink()

        assert os.readlink(default) == str(tmp_path / "gui-sock-old")

    def test_runs_only_once(self, monkeypatch):
        monkeypatch.delenv("KAKU_UNIX_SOCKET", raising=False)
        KakuPaneBackend._heal_socket_symlink()
        assert KakuPaneBackend._socket_healed is True
        # Second call is a no-op (flag already set)
        KakuPaneBackend._heal_socket_symlink()
