from __future__ import annotations

import sys
import time

import pytest

from openmax.pane_backend import (
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

    # With neither kaku nor tmux available, auto-detect falls back to "kaku"
    resolved = resolve_pane_backend_name()
    assert resolved in ("kaku", "tmux")


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
