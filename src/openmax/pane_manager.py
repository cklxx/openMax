"""Kaku pane manager — tracks windows, panes, and their relationships."""

from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import unquote, urlparse

_KAKU_CLI_PREFIX = ["kaku", "cli"]


def _wrap_command_clean_env(command: list[str]) -> list[str]:
    """Wrap a command to run without CLAUDECODE env var."""
    return ["env", "-u", "CLAUDECODE", "-u", "CLAUDE_CODE_ENTRYPOINT"] + command


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


@dataclass
class KakuPaneInfo:
    """Raw pane info from kaku cli list."""

    window_id: int
    tab_id: int
    pane_id: int
    workspace: str
    rows: int
    cols: int
    title: str
    cwd: str
    is_active: bool
    is_zoomed: bool
    cursor_visibility: str


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

    def __init__(self) -> None:
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
        result = PaneManager._run_kaku(["list", "--format", "json"])
        raw = json.loads(result.stdout)
        panes = []
        for p in raw:
            cwd = p.get("cwd", "")
            if cwd.startswith("file://"):
                cwd = unquote(urlparse(cwd).path)
            panes.append(
                KakuPaneInfo(
                    window_id=p["window_id"],
                    tab_id=p["tab_id"],
                    pane_id=p["pane_id"],
                    workspace=p.get("workspace", ""),
                    rows=p["size"]["rows"],
                    cols=p["size"]["cols"],
                    title=p.get("title", ""),
                    cwd=cwd,
                    is_active=p.get("is_active", False),
                    is_zoomed=p.get("is_zoomed", False),
                    cursor_visibility=p.get("cursor_visibility", ""),
                )
            )
        return panes

    # ── High-level: create window with first pane ──────────────────

    def create_window(
        self,
        command: list[str],
        purpose: str,
        agent_type: str,
        title: str = "openMax agents",
        cwd: str | None = None,
    ) -> ManagedPane:
        """Create a NEW window, run command in it, track everything.

        Returns the ManagedPane for the first pane.
        """
        args = ["spawn", "--new-window"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))

        result = self._run_kaku(args)
        pane_id = int(result.stdout.strip())
        window_id = self._find_pane_window(pane_id)

        # Track window
        win = ManagedWindow(
            window_id=window_id,
            title=title,
            pane_ids=[pane_id],
        )
        if window_id is not None:
            self._windows[window_id] = win
            self._set_window_title(pane_id, title)
            # Focus the agent pane so its window becomes frontmost,
            # then resize window 1 (which is now the agent window).
            self.activate_pane(pane_id)
            time.sleep(0.3)
            self._resize_frontmost_window()

        # Track pane
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
        cwd: str | None = None,
    ) -> ManagedPane:
        """Add a new pane to an existing managed window with smart layout.

        Automatically picks the split target and direction based on
        how many panes are already in the window.
        """
        win = self._windows.get(window_id)
        if win is None:
            raise RuntimeError(f"Window {window_id} is not managed")

        index = len(win.pane_ids)
        target_pane, direction = _pick_split(win.pane_ids, index)

        args = ["split-pane"]
        args.extend(["--pane-id", str(target_pane)])
        if direction == "right":
            args.append("--right")
        elif direction == "bottom":
            args.append("--bottom")
        elif direction == "left":
            args.append("--left")
        elif direction == "top":
            args.append("--top")
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))

        result = self._run_kaku(args)
        pane_id = int(result.stdout.strip())

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
        self._run_kaku(["send-text", "--pane-id", str(pane_id), "--", content])

        if submit:
            time.sleep(0.5)
            self._run_kaku(
                ["send-text", "--pane-id", str(pane_id), "--no-paste"],
                input_text="\r",
            )

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        """Read text content from a pane."""
        args = ["get-text", "--pane-id", str(pane_id)]
        if start_line is not None:
            args.extend(["--start-line", str(start_line)])
        result = self._run_kaku(args)
        return result.stdout

    def activate_pane(self, pane_id: int) -> None:
        """Focus a pane."""
        self._run_kaku(["activate-pane", "--pane-id", str(pane_id)], check=False)

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
            return any(p.pane_id == pane_id for p in self.list_all_panes())
        except RuntimeError:
            return False

    def refresh_states(self) -> None:
        """Check all managed panes and update states for dead ones."""
        try:
            alive_ids = {p.pane_id for p in self.list_all_panes()}
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
        """Kill all managed panes. Windows close automatically when empty."""
        managed_pane_ids = list(self._panes)
        for pane_id in managed_pane_ids:
            self._kill_pane_process(pane_id)

        # Verify: some panes may survive the first kill (e.g. interactive CLIs
        # that trap signals).  Re-check and retry once.
        time.sleep(0.5)
        try:
            alive_ids = {p.pane_id for p in self.list_all_panes()}
        except RuntimeError:
            alive_ids = set()

        stragglers = alive_ids & set(managed_pane_ids)
        for pane_id in stragglers:
            self._kill_pane_process(pane_id)

        self._panes.clear()
        self._windows.clear()

    def __enter__(self) -> PaneManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup_all()
        return None

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _run_kaku(
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*_KAKU_CLI_PREFIX, *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            command_name = " ".join(args[:2]).strip()
            raise RuntimeError(f"kaku {command_name} failed: {result.stderr}")
        return result

    def _find_pane_window(self, pane_id: int) -> int | None:
        try:
            for p in self.list_all_panes():
                if p.pane_id == pane_id:
                    return p.window_id
        except RuntimeError:
            pass
        return None

    def _set_window_title(self, pane_id: int, title: str) -> None:
        self._run_kaku(
            ["set-window-title", "--pane-id", str(pane_id), title],
            check=False,
        )

    def _kill_pane_process(self, pane_id: int) -> None:
        self._run_kaku(["kill-pane", "--pane-id", str(pane_id)], check=False)

    @staticmethod
    def _resize_frontmost_window() -> None:
        """Resize the frontmost kaku window to 50% of screen (macOS only)."""
        if platform.system() != "Darwin":
            return
        script = (
            'tell application "Finder"\n'
            "  set {_x, _y, sw, sh} to bounds of window of desktop\n"
            "end tell\n"
            "set w to round (sw * 0.5)\n"
            "set h to round (sh * 0.5)\n"
            "set xOff to round ((sw - w) / 2)\n"
            "set yOff to round ((sh - h) / 2)\n"
            'tell application "System Events"\n'
            '  tell process "kaku-gui"\n'
            "    set position of window 1 to {xOff, yOff}\n"
            "    set size of window 1 to {w, h}\n"
            "  end tell\n"
            "end tell"
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
