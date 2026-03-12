from __future__ import annotations

from types import SimpleNamespace

from openmax.pane_manager import ManagedPane, ManagedWindow, PaneManager, PaneState


def test_cleanup_all_retries_tracked_stragglers(monkeypatch):
    manager = PaneManager()
    manager._panes = {
        11: ManagedPane(11, 5, "API", "codex", PaneState.RUNNING),
        12: ManagedPane(12, 5, "UI", "claude-code", PaneState.RUNNING),
    }
    manager._windows = {5: ManagedWindow(5, "openMax", [11, 12])}

    killed: list[int] = []
    monkeypatch.setattr(manager, "_kill_pane_process", lambda pane_id: killed.append(pane_id))
    monkeypatch.setattr("openmax.pane_manager.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        PaneManager,
        "list_all_panes",
        staticmethod(
            lambda: [
                SimpleNamespace(pane_id=12),
                SimpleNamespace(pane_id=99),
            ]
        ),
    )

    manager.cleanup_all()

    assert killed == [11, 12, 12]
    assert manager.panes == {}
    assert manager.windows == {}
