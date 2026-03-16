"""Kaku pane manager — tracks windows, panes, and their relationships."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from openmax.pane_backend import KakuPaneBackend, PaneBackend, PaneInfo, create_pane_backend


class PaneState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class ManagedPane:
    """A pane managed by openMax."""

    pane_id: int
    window_id: int | None
    purpose: str
    agent_type: str
    state: PaneState = PaneState.IDLE
    created_at: float = field(default_factory=time.time)


@dataclass
class ManagedWindow:
    """A window managed by openMax, containing one or more panes."""

    window_id: int
    title: str
    pane_ids: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


KakuPaneInfo = PaneInfo


# ── Layout strategy ───────────────────────────────────────────────

# How to split panes given the count already in the window:
#   0 panes → first pane (spawn window)
#   1 pane  → split right  (left | right)
#   2 panes → split pane[1] bottom  (left | right-top / right-bottom)
#   3 panes → split pane[0] bottom  (left-top / left-bottom | right-top / right-bottom)
#   4+ panes → alternate bottom on most recent panes


def _pick_split(pane_ids: list[int], index: int) -> tuple[int, str]:
    """Given existing pane_ids and the new pane index, return (target_pane_id, direction)."""
    if index == 0:
        raise ValueError("index 0 should use spawn_window, not split")
    if index == 1:
        return pane_ids[0], "right"
    if index == 2:
        return pane_ids[1], "bottom"
    if index == 3:
        return pane_ids[0], "bottom"
    # 4+: cycle bottom on existing panes
    target = pane_ids[index % len(pane_ids)]
    return target, "bottom"


class PaneManager:
    """Manages kaku windows and panes for openMax.

    Tracks:
    - Which windows we created and their IDs
    - Which panes belong to which window
    - Pane states (idle/running/done/error)
    - Layout: how panes are arranged within a window
    """

    def __init__(
        self,
        backend: PaneBackend | None = None,
        backend_name: str | None = None,
    ) -> None:
        if backend is not None and backend_name is not None:
            raise ValueError("Pass either backend or backend_name, not both")
        self._backend = backend or create_pane_backend(backend_name)
        self._panes: dict[int, ManagedPane] = {}
        self._windows: dict[int, ManagedWindow] = {}

    # ── Properties ─────────────────────────────────────────────────

    @property
    def panes(self) -> dict[int, ManagedPane]:
        return dict(self._panes)

    @property
    def windows(self) -> dict[int, ManagedWindow]:
        return dict(self._windows)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._panes.values() if p.state == PaneState.RUNNING)

    # ── Kaku CLI wrappers ──────────────────────────────────────────

    @staticmethod
    def list_all_panes() -> list[KakuPaneInfo]:
        """List all kaku panes (not just managed ones)."""
        return KakuPaneBackend().list_panes()

    # ── High-level: create window with first pane ──────────────────

    def create_window(
        self,
        command: list[str],
        purpose: str,
        agent_type: str,
        title: str = "openMax agents",
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ManagedPane:
        """Create a NEW window, run command in it, track everything.

        Returns the ManagedPane for the first pane.
        """
        if env:
            pane_id = self._backend.spawn_window(command, cwd=cwd, env=env)
        else:
            pane_id = self._backend.spawn_window(command, cwd=cwd)

        # Resolve window_id with retries (pane may not appear in list
        # immediately after spawn).
        window_id = self._find_pane_window(pane_id)

        # Always track the window — use pane_id as synthetic window_id
        # when the real one cannot be determined (e.g. the pane exited
        # before we could query it).
        effective_window_id = window_id if window_id is not None else pane_id
        win = ManagedWindow(
            window_id=effective_window_id,
            title=title,
            pane_ids=[pane_id],
        )
        self._windows[effective_window_id] = win

        if window_id is not None:
            self._set_window_title(pane_id, title)
            # Focus the agent pane so its window becomes frontmost,
            # then resize window 1 (which is now the agent window).
            self.activate_pane(pane_id)
            time.sleep(0.3)
            self._backend.resize_frontmost_window()

        # Track pane
        pane = ManagedPane(
            pane_id=pane_id,
            window_id=effective_window_id,
            purpose=purpose,
            agent_type=agent_type,
            state=PaneState.RUNNING,
        )
        self._panes[pane_id] = pane
        return pane

    # ── High-level: add pane to existing window ────────────────────

    def add_pane(
        self,
        window_id: int,
        command: list[str],
        purpose: str,
        agent_type: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ManagedPane:
        """Add a new pane to an existing managed window with smart layout.

        Automatically picks the split target and direction based on
        how many panes are already in the window.  If all existing
        panes in the window have died, falls back to creating a new
        window instead.
        """
        win = self._windows.get(window_id)
        if win is None:
            raise RuntimeError(f"Window {window_id} is not managed")

        # Prune dead panes so split targets are valid
        alive_ids = set()
        try:
            alive_ids = {p.pane_id for p in self._list_all_panes()}
        except RuntimeError:
            pass
        win.pane_ids = [pid for pid in win.pane_ids if pid in alive_ids]

        if not win.pane_ids:
            # All panes in window have died — create a fresh window
            return self.create_window(
                command=command,
                purpose=purpose,
                agent_type=agent_type,
                title=win.title,
                cwd=cwd,
                env=env,
            )

        index = len(win.pane_ids)
        target_pane, direction = _pick_split(win.pane_ids, index)

        if env:
            pane_id = self._backend.split_pane(
                target_pane,
                direction,
                command,
                cwd=cwd,
                env=env,
            )
        else:
            pane_id = self._backend.split_pane(
                target_pane,
                direction,
                command,
                cwd=cwd,
            )

        # Track
        win.pane_ids.append(pane_id)
        pane = ManagedPane(
            pane_id=pane_id,
            window_id=window_id,
            purpose=purpose,
            agent_type=agent_type,
            state=PaneState.RUNNING,
        )
        self._panes[pane_id] = pane
        return pane

    # ── Pane I/O ───────────────────────────────────────────────────

    def send_text(self, pane_id: int, text: str, submit: bool = True) -> None:
        """Send text to a pane as paste, then optionally press Enter."""
        content = text.rstrip("\n").rstrip("\r")
        self._backend.send_text(pane_id, content)

        if submit:
            time.sleep(0.5)
            self._backend.send_enter(pane_id)

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        """Read text content from a pane."""
        return self._backend.get_text(pane_id, start_line=start_line)

    def activate_pane(self, pane_id: int) -> None:
        """Focus a pane."""
        self._backend.activate_pane(pane_id)

    def kill_pane(self, pane_id: int) -> None:
        """Kill a pane and remove from tracking."""
        self._kill_pane_process(pane_id)
        # Remove from window tracking
        pane = self._panes.pop(pane_id, None)
        if pane and pane.window_id and pane.window_id in self._windows:
            win = self._windows[pane.window_id]
            if pane_id in win.pane_ids:
                win.pane_ids.remove(pane_id)

    # ── State management ───────────────────────────────────────────

    def update_state(self, pane_id: int, state: PaneState) -> None:
        if pane_id in self._panes:
            self._panes[pane_id].state = state

    def is_pane_alive(self, pane_id: int) -> bool:
        try:
            return any(p.pane_id == pane_id for p in self._list_all_panes())
        except RuntimeError:
            return False

    def refresh_states(self) -> None:
        """Check all managed panes and update states for dead ones."""
        try:
            alive_ids = {p.pane_id for p in self._list_all_panes()}
        except RuntimeError:
            return
        for pane_id, pane in list(self._panes.items()):
            if pane_id not in alive_ids:
                pane.state = PaneState.DONE

    def summary(self) -> dict:
        """Full topology: windows → panes with states."""
        window_list = []
        for win in self._windows.values():
            pane_details = []
            for pid in win.pane_ids:
                p = self._panes.get(pid)
                if p:
                    pane_details.append(
                        {
                            "pane_id": p.pane_id,
                            "purpose": p.purpose,
                            "agent_type": p.agent_type,
                            "state": p.state.value,
                        }
                    )
            window_list.append(
                {
                    "window_id": win.window_id,
                    "title": win.title,
                    "pane_count": len(win.pane_ids),
                    "panes": pane_details,
                }
            )

        return {
            "total_windows": len(self._windows),
            "total_panes": len(self._panes),
            "running": sum(1 for p in self._panes.values() if p.state == PaneState.RUNNING),
            "done": sum(1 for p in self._panes.values() if p.state == PaneState.DONE),
            "error": sum(1 for p in self._panes.values() if p.state == PaneState.ERROR),
            "windows": window_list,
        }

    # ── Cleanup ────────────────────────────────────────────────────

    def cleanup_all(self) -> None:
        """Kill all managed panes and ensure managed windows close.

        After killing managed panes, any panes still belonging to our
        managed windows are also killed (e.g. replacement shells spawned
        by the terminal).  Kaku closes a window once its last pane dies.
        """
        managed_pane_ids = list(self._panes)
        managed_window_ids = set(self._windows)

        for pane_id in managed_pane_ids:
            self._kill_pane_process(pane_id)

        # Verify: some panes may survive the first kill (e.g. interactive
        # CLIs that trap signals).  Re-check and retry once.
        time.sleep(0.5)
        try:
            alive_panes = self._list_all_panes()
        except RuntimeError:
            alive_panes = []

        # Retry stragglers from our managed set
        stragglers = [p for p in alive_panes if p.pane_id in managed_pane_ids]
        for p in stragglers:
            self._kill_pane_process(p.pane_id)

        # Kill any panes still sitting in our managed windows (replacement
        # shells or panes spawned by the terminal after we killed ours).
        # Re-list to catch panes Kaku may have created after our kills.
        if managed_window_ids:
            time.sleep(0.3)
            try:
                still_alive = self._list_all_panes()
            except RuntimeError:
                still_alive = []
            for p in still_alive:
                if p.window_id in managed_window_ids:
                    self._kill_pane_process(p.pane_id)

        self._panes.clear()
        self._windows.clear()

    def __enter__(self) -> PaneManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup_all()
        return None

    # ── Internal helpers ───────────────────────────────────────────

    def _list_all_panes(self) -> list[KakuPaneInfo]:
        return self._backend.list_panes()

    def _find_pane_window(
        self,
        pane_id: int,
        retries: int = 3,
        delay: float = 0.3,
    ) -> int | None:
        """Find the window that contains *pane_id*.

        Retries a few times to handle the race between ``spawn_window``
        returning and the pane actually appearing in ``list_panes``.
        """
        for attempt in range(retries):
            try:
                for p in self._list_all_panes():
                    if p.pane_id == pane_id:
                        return p.window_id
            except RuntimeError:
                pass
            if attempt < retries - 1:
                time.sleep(delay)
        return None

    def _set_window_title(self, pane_id: int, title: str) -> None:
        self._backend.set_window_title(pane_id, title)

    def _kill_pane_process(self, pane_id: int) -> None:
        self._backend.kill_pane(pane_id)
