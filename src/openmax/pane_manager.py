"""Pane manager — tracks windows, panes, and their relationships."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openmax.pane_backend import PaneBackend, PaneInfo, create_pane_backend

_LIST_PANES_TTL = 1.0  # seconds — cache list_panes() results to avoid repeated subprocess calls


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
    external: bool = False  # True = attached from a pre-existing pane, never killed on cleanup


@dataclass
class ManagedWindow:
    """A window managed by openMax, containing one or more panes."""

    window_id: int
    title: str
    pane_ids: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    external: bool = False  # True = pre-existing window, never killed on cleanup


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
    """Manages terminal windows and panes for openMax.

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
        self._last_output: dict[int, str] = {}  # pane_id -> last successful get_text
        self._cached_panes: list[PaneInfo] = []
        self._cached_panes_at: float = 0.0

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

    # ── Backend CLI wrappers ─────────────────────────────────────────

    @staticmethod
    def list_all_panes() -> list[PaneInfo]:
        """List all panes from the active backend (not just managed ones)."""
        return create_pane_backend().list_panes()

    def list_backend_panes(self, *, force: bool = False) -> list[PaneInfo]:
        """List panes visible to this manager's backend instance."""
        return list(self._list_all_panes(force=force))

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
        """Create a NEW window, run command in it, track everything."""
        pane_id = self._backend.spawn_window(command, cwd=cwd, env=env)
        window_id = self._find_pane_window(pane_id)
        effective_wid = window_id if window_id is not None else pane_id
        self._track_window(effective_wid, title, pane_id)
        if window_id is not None:
            self._focus_and_resize(pane_id, title)
        return self._track_pane(pane_id, effective_wid, purpose, agent_type)

    def attach_pane(self, pane_info: PaneInfo, purpose: str) -> ManagedPane:
        """Register a pre-existing pane into management without launching it."""
        wid = pane_info.window_id
        if wid not in self._windows:
            self._windows[wid] = ManagedWindow(window_id=wid, title=f"window-{wid}", external=True)
        win = self._windows[wid]
        if pane_info.pane_id not in win.pane_ids:
            win.pane_ids.append(pane_info.pane_id)
        pane = ManagedPane(
            pane_id=pane_info.pane_id,
            window_id=wid,
            purpose=purpose,
            agent_type="external",
            state=PaneState.RUNNING,
            external=True,
        )
        self._panes[pane_info.pane_id] = pane
        return pane

    def _track_window(self, window_id: int, title: str, first_pane_id: int) -> None:
        win = ManagedWindow(window_id=window_id, title=title, pane_ids=[first_pane_id])
        self._windows[window_id] = win

    def _focus_and_resize(self, pane_id: int, title: str) -> None:
        self._set_window_title(pane_id, title)
        self.activate_pane(pane_id)
        time.sleep(0.3)
        self._backend.resize_frontmost_window()

    def _track_pane(
        self,
        pane_id: int,
        window_id: int,
        purpose: str,
        agent_type: str,
    ) -> ManagedPane:
        pane = ManagedPane(
            pane_id=pane_id,
            window_id=window_id,
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
        title: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ManagedPane:
        """Add a new pane to an existing managed window with smart layout."""
        win = self._windows.get(window_id)
        if win is None:
            raise RuntimeError(f"Window {window_id} is not managed")
        self._prune_dead_panes(win)
        if not win.pane_ids:
            return self.create_window(
                command=command,
                purpose=purpose,
                agent_type=agent_type,
                title=win.title,
                cwd=cwd,
                env=env,
            )
        target_pane, direction = _pick_split(win.pane_ids, len(win.pane_ids))
        pane_id = self._backend.split_pane(
            target_pane,
            direction,
            command,
            cwd=cwd,
            env=env,
        )
        win.pane_ids.append(pane_id)
        return self._track_pane(pane_id, window_id, purpose, agent_type)

    def _prune_dead_panes(self, win: ManagedWindow) -> None:
        """Remove pane IDs that no longer exist from a window's list."""
        try:
            alive_ids = {p.pane_id for p in self._list_all_panes()}
        except RuntimeError:
            return
        win.pane_ids = [pid for pid in win.pane_ids if pid in alive_ids]

    # ── Pane I/O ───────────────────────────────────────────────────

    def send_text(self, pane_id: int, text: str, submit: bool = True) -> None:
        """Send text to a pane as paste, then optionally press Enter."""
        content = text.rstrip("\n").rstrip("\r")
        self._backend.send_text(pane_id, content)

        if submit:
            time.sleep(0.15)
            self._backend.send_enter(pane_id)

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        """Read text content from a pane. Caches output so dead panes still return data."""
        try:
            text = self._backend.get_text(pane_id, start_line=start_line)
            # Cache every successful read so we can return it after pane dies.
            self._last_output[pane_id] = text
            return text
        except Exception:
            # Pane gone — return cached output if available.
            cached = self._last_output.get(pane_id)
            if cached is not None:
                return cached
            raise

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
            return any(p.pane_id == pane_id for p in self._list_all_panes(force=True))
        except RuntimeError:
            return False

    def refresh_states(self, *, force: bool = False) -> None:
        """Check all managed panes and update states for dead ones."""
        try:
            alive_ids = {p.pane_id for p in self._list_all_panes(force=force)}
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

    def all_panes_summary(self, *, force: bool = False) -> dict:
        """Summarize all panes visible to this backend, annotating managed ones."""
        visible_panes = self.list_backend_panes(force=force)
        managed_summary = self.summary()
        windows: dict[int, dict[str, Any]] = {}

        for info in visible_panes:
            tracked = self._panes.get(info.pane_id)
            window = windows.setdefault(
                info.window_id,
                {
                    "window_id": info.window_id,
                    "title": self._windows.get(info.window_id).title
                    if info.window_id in self._windows
                    else "",
                    "pane_count": 0,
                    "panes": [],
                },
            )
            pane_entry: dict[str, Any] = {
                "pane_id": info.pane_id,
                "title": info.title,
                "cwd": info.cwd,
                "workspace": info.workspace,
                "tab_id": info.tab_id,
                "active": info.is_active,
                "managed": tracked is not None,
                "state": tracked.state.value if tracked is not None else "unmanaged",
            }
            if tracked is not None:
                pane_entry["purpose"] = tracked.purpose
                pane_entry["agent_type"] = tracked.agent_type
                pane_entry["external"] = tracked.external
            window["panes"].append(pane_entry)

        window_list: list[dict[str, Any]] = []
        for window_id in sorted(windows):
            window = windows[window_id]
            panes = sorted(window["panes"], key=lambda pane: pane["pane_id"])
            window["panes"] = panes
            window["pane_count"] = len(panes)
            if not window["title"]:
                first_title = next((pane["title"] for pane in panes if pane["title"]), "")
                window["title"] = first_title or f"window-{window_id}"
            window_list.append(window)

        total_visible = len(visible_panes)
        visible_managed = [self._panes[info.pane_id] for info in visible_panes if info.pane_id in self._panes]
        managed_total = len(visible_managed)
        return {
            "total_windows": len(window_list),
            "total_panes": total_visible,
            "managed_panes": managed_total,
            "unmanaged_panes": max(total_visible - managed_total, 0),
            "running": sum(1 for pane in visible_managed if pane.state == PaneState.RUNNING),
            "done": sum(1 for pane in visible_managed if pane.state == PaneState.DONE),
            "error": sum(1 for pane in visible_managed if pane.state == PaneState.ERROR),
            "windows": window_list,
            "managed": managed_summary,
        }

    # ── Cleanup ────────────────────────────────────────────────────

    def cleanup_all(self) -> None:
        """Kill all managed panes and ensure managed windows close. Skips external panes."""
        managed_pane_ids = [pid for pid, p in self._panes.items() if not p.external]
        managed_window_ids = {wid for wid, w in self._windows.items() if not w.external}
        for pane_id in managed_pane_ids:
            self._kill_pane_process(pane_id)
        self._kill_stragglers(managed_pane_ids)
        self._kill_window_remnants(managed_window_ids)
        self._panes.clear()
        self._windows.clear()

    def _kill_stragglers(self, managed_pane_ids: list[int]) -> None:
        """Retry killing managed panes that survived the first attempt."""
        time.sleep(0.5)
        for p in self._safe_list_panes(force=True):
            if p.pane_id in managed_pane_ids:
                self._kill_pane_process(p.pane_id)

    def _kill_window_remnants(self, window_ids: set[int]) -> None:
        """Kill replacement shells the terminal spawned in our windows."""
        if not window_ids:
            return
        time.sleep(0.3)
        for p in self._safe_list_panes(force=True):
            if p.window_id in window_ids:
                self._kill_pane_process(p.pane_id)

    def _safe_list_panes(self, *, force: bool = False) -> list[PaneInfo]:
        try:
            return self._list_all_panes(force=force)
        except RuntimeError:
            return []

    def __enter__(self) -> PaneManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup_all()
        return None

    # ── Internal helpers ───────────────────────────────────────────

    def _list_all_panes(self, *, force: bool = False) -> list[PaneInfo]:
        now = time.monotonic()
        if not force and (now - self._cached_panes_at) < _LIST_PANES_TTL:
            return self._cached_panes
        self._cached_panes = self._backend.list_panes()
        self._cached_panes_at = now
        return self._cached_panes

    def _find_pane_window(
        self,
        pane_id: int,
        retries: int = 3,
        delay: float = 0.1,
    ) -> int | None:
        """Find the window that contains *pane_id*.

        Retries a few times to handle the race between ``spawn_window``
        returning and the pane actually appearing in ``list_panes``.
        """
        for attempt in range(retries):
            try:
                for p in self._list_all_panes(force=True):
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
