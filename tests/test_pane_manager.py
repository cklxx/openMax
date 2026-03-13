from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

import pytest

from openmax.pane_backend import HeadlessPaneBackend, KakuPaneBackend, PaneInfo
from openmax.pane_manager import ManagedPane, ManagedWindow, PaneManager, PaneState


def make_pane_info(pane_id: int, window_id: int) -> PaneInfo:
    return PaneInfo(
        window_id=window_id,
        tab_id=1,
        pane_id=pane_id,
        workspace="",
        rows=24,
        cols=80,
        title="",
        cwd="/tmp",
        is_active=False,
        is_zoomed=False,
        cursor_visibility="visible",
    )


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.spawned_pane_id = 11
        self.split_pane_id = 13
        self.list_panes_result: list[PaneInfo] = []
        self.text_result = "pane output"

    def list_panes(self) -> list[PaneInfo]:
        self.calls.append(("list_panes",))
        return list(self.list_panes_result)

    def spawn_window(self, command: list[str], cwd: str | None = None) -> int:
        self.calls.append(("spawn_window", command, cwd))
        return self.spawned_pane_id

    def split_pane(
        self,
        target_pane_id: int,
        direction: str,
        command: list[str],
        cwd: str | None = None,
    ) -> int:
        self.calls.append(("split_pane", target_pane_id, direction, command, cwd))
        return self.split_pane_id

    def send_text(self, pane_id: int, text: str) -> None:
        self.calls.append(("send_text", pane_id, text))

    def send_enter(self, pane_id: int) -> None:
        self.calls.append(("send_enter", pane_id))

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        self.calls.append(("get_text", pane_id, start_line))
        return self.text_result

    def activate_pane(self, pane_id: int) -> None:
        self.calls.append(("activate_pane", pane_id))

    def set_window_title(self, pane_id: int, title: str) -> None:
        self.calls.append(("set_window_title", pane_id, title))

    def kill_pane(self, pane_id: int) -> None:
        self.calls.append(("kill_pane", pane_id))

    def resize_frontmost_window(self) -> None:
        self.calls.append(("resize_frontmost_window",))


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def test_create_window_tracks_window_and_uses_backend(monkeypatch):
    backend = FakeBackend()
    backend.list_panes_result = [make_pane_info(pane_id=11, window_id=5)]
    manager = PaneManager(backend=backend)
    monkeypatch.setattr("openmax.pane_manager.time.sleep", lambda _seconds: None)

    pane = manager.create_window(
        command=["codex", "exec"],
        purpose="API",
        agent_type="codex",
        title="openMax agents",
        cwd="/repo",
    )

    assert pane == ManagedPane(11, 5, "API", "codex", PaneState.RUNNING, pane.created_at)
    assert manager.windows == {5: ManagedWindow(5, "openMax agents", [11], manager.windows[5].created_at)}
    assert backend.calls == [
        ("spawn_window", ["codex", "exec"], "/repo"),
        ("list_panes",),
        ("set_window_title", 11, "openMax agents"),
        ("activate_pane", 11),
        ("resize_frontmost_window",),
    ]


def test_add_pane_uses_split_strategy_via_backend():
    backend = FakeBackend()
    backend.split_pane_id = 14
    manager = PaneManager(backend=backend)
    manager._windows = {5: ManagedWindow(5, "openMax", [11, 12])}

    pane = manager.add_pane(
        window_id=5,
        command=["claude"],
        purpose="UI",
        agent_type="claude-code",
        cwd="/repo",
    )

    assert pane.pane_id == 14
    assert pane.window_id == 5
    assert manager.windows[5].pane_ids == [11, 12, 14]
    assert backend.calls == [
        ("split_pane", 12, "bottom", ["claude"], "/repo"),
    ]


def test_send_text_trims_trailing_newlines_and_submits(monkeypatch):
    backend = FakeBackend()
    manager = PaneManager(backend=backend)
    monkeypatch.setattr("openmax.pane_manager.time.sleep", lambda _seconds: None)

    manager.send_text(11, "hello world\r\n", submit=True)

    assert backend.calls == [
        ("send_text", 11, "hello world"),
        ("send_enter", 11),
    ]


def test_cleanup_all_retries_tracked_stragglers(monkeypatch):
    backend = FakeBackend()
    manager = PaneManager(backend=backend)
    manager._panes = {
        11: ManagedPane(11, 5, "API", "codex", PaneState.RUNNING),
        12: ManagedPane(12, 5, "UI", "claude-code", PaneState.RUNNING),
    }
    manager._windows = {5: ManagedWindow(5, "openMax", [11, 12])}
    backend.list_panes_result = [
        make_pane_info(pane_id=12, window_id=5),
        make_pane_info(pane_id=99, window_id=8),
    ]
    monkeypatch.setattr("openmax.pane_manager.time.sleep", lambda _seconds: None)

    manager.cleanup_all()

    assert backend.calls == [
        ("kill_pane", 11),
        ("kill_pane", 12),
        ("list_panes",),
        ("kill_pane", 12),
    ]
    assert manager.panes == {}
    assert manager.windows == {}


def test_headless_backend_manager_tracks_windows_and_cleans_up(monkeypatch):
    backend = HeadlessPaneBackend()
    manager = PaneManager(backend=backend)
    monkeypatch.setattr("openmax.pane_manager.time.sleep", lambda _seconds: None)

    command = [
        sys.executable,
        "-u",
        "-c",
        "import time; time.sleep(30)",
    ]

    first = manager.create_window(
        command=command,
        purpose="API",
        agent_type="codex",
        title="headless",
        cwd="/tmp",
    )
    second = manager.add_pane(
        window_id=first.window_id,
        command=command,
        purpose="UI",
        agent_type="claude-code",
        cwd="/tmp",
    )

    _wait_until(lambda: len(backend.list_panes()) == 2)

    summary = manager.summary()

    assert first.window_id is not None
    assert second.window_id == first.window_id
    assert summary["total_windows"] == 1
    assert summary["total_panes"] == 2
    assert summary["running"] == 2
    assert summary["windows"][0]["pane_count"] == 2
    assert [pane["purpose"] for pane in summary["windows"][0]["panes"]] == ["API", "UI"]

    monkeypatch.setattr(
        "openmax.pane_manager.KakuPaneBackend.list_panes",
        lambda self: pytest.fail("cleanup_all should use the injected backend"),
    )
    manager.cleanup_all()

    _wait_until(lambda: backend.list_panes() == [])
    assert manager.panes == {}
    assert manager.windows == {}


def test_kaku_backend_spawn_window_preserves_command_wrapping(monkeypatch):
    backend = KakuPaneBackend()
    calls: list[tuple] = []

    def fake_run_kaku(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="21\n")

    monkeypatch.setattr(backend, "_run_kaku", fake_run_kaku)

    pane_id = backend.spawn_window(["codex", "exec"], cwd="/repo")

    assert pane_id == 21
    assert calls == [
        (
            [
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
            ],
            {},
        )
    ]


def test_kaku_backend_list_panes_decodes_file_urls(monkeypatch):
    backend = KakuPaneBackend()
    payload = json.dumps(
        [
            {
                "window_id": 5,
                "tab_id": 2,
                "pane_id": 11,
                "workspace": "main",
                "size": {"rows": 24, "cols": 80},
                "title": "agent",
                "cwd": "file:///tmp/openmax%20repo",
                "is_active": True,
                "is_zoomed": False,
                "cursor_visibility": "visible",
            }
        ]
    )

    monkeypatch.setattr(
        backend,
        "_run_kaku",
        lambda args, **kwargs: SimpleNamespace(stdout=payload),
    )

    panes = backend.list_panes()

    assert panes == [
        PaneInfo(
            window_id=5,
            tab_id=2,
            pane_id=11,
            workspace="main",
            rows=24,
            cols=80,
            title="agent",
            cwd="/tmp/openmax repo",
            is_active=True,
            is_zoomed=False,
            cursor_visibility="visible",
        )
    ]
