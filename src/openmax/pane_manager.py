"""Kaku pane manager — tracks all panes created by openMax."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import unquote, urlparse


def _wrap_command_clean_env(command: list[str]) -> list[str]:
    """Wrap a command to run without CLAUDECODE env var.

    This prevents 'nested session' errors when spawning
    Claude Code in new kaku panes.
    """
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
    purpose: str  # e.g. "lead-agent", "subtask: Write components"
    agent_type: str  # e.g. "claude-code", "codex"
    state: PaneState = PaneState.IDLE
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


class PaneManager:
    """Manages kaku windows and panes for openMax."""

    def __init__(self) -> None:
        self._managed: dict[int, ManagedPane] = {}
        self._windows: set[int] = set()  # window IDs we created

    @property
    def panes(self) -> dict[int, ManagedPane]:
        return dict(self._managed)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._managed.values() if p.state == PaneState.RUNNING)

    @property
    def idle_panes(self) -> list[ManagedPane]:
        return [p for p in self._managed.values() if p.state == PaneState.IDLE]

    @property
    def managed_windows(self) -> set[int]:
        return set(self._windows)

    # ── Kaku CLI wrappers ──────────────────────────────────────────

    @staticmethod
    def list_all_panes() -> list[KakuPaneInfo]:
        """List all kaku panes (not just managed ones)."""
        result = subprocess.run(
            ["kaku", "cli", "list", "--format", "json"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"kaku cli list failed: {result.stderr}")
        raw = json.loads(result.stdout)
        panes = []
        for p in raw:
            cwd = p.get("cwd", "")
            if cwd.startswith("file://"):
                cwd = unquote(urlparse(cwd).path)
            panes.append(KakuPaneInfo(
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
            ))
        return panes

    # ── Window management ──────────────────────────────────────────

    def spawn_window(
        self,
        command: list[str],
        purpose: str,
        agent_type: str,
        cwd: str | None = None,
    ) -> ManagedPane:
        """Open a NEW window with a command and track it.

        Returns the ManagedPane for the pane in the new window.
        """
        args = ["kaku", "cli", "spawn", "--new-window"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"kaku spawn --new-window failed: {result.stderr}")

        pane_id = int(result.stdout.strip())

        # Find which window this pane belongs to
        window_id = self._find_pane_window(pane_id)
        if window_id is not None:
            self._windows.add(window_id)

        pane = ManagedPane(
            pane_id=pane_id,
            window_id=window_id,
            purpose=purpose,
            agent_type=agent_type,
            state=PaneState.RUNNING,
        )
        self._managed[pane_id] = pane
        return pane

    def spawn_tab(
        self,
        command: list[str],
        purpose: str,
        agent_type: str,
        cwd: str | None = None,
        window_id: int | None = None,
    ) -> ManagedPane:
        """Open a new tab (in an existing window or default) and track it."""
        args = ["kaku", "cli", "spawn"]
        if window_id is not None:
            args.extend(["--window-id", str(window_id)])
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"kaku spawn failed: {result.stderr}")

        pane_id = int(result.stdout.strip())
        win_id = window_id or self._find_pane_window(pane_id)

        pane = ManagedPane(
            pane_id=pane_id,
            window_id=win_id,
            purpose=purpose,
            agent_type=agent_type,
            state=PaneState.RUNNING,
        )
        self._managed[pane_id] = pane
        return pane

    def split_pane(
        self,
        command: list[str],
        purpose: str,
        agent_type: str,
        direction: str = "right",
        cwd: str | None = None,
        target_pane_id: int | None = None,
    ) -> ManagedPane:
        """Split an existing pane and track the new one."""
        args = ["kaku", "cli", "split-pane"]
        if target_pane_id is not None:
            args.extend(["--pane-id", str(target_pane_id)])
        if direction == "right":
            args.append("--right")
        elif direction == "left":
            args.append("--left")
        elif direction == "bottom":
            args.append("--bottom")
        elif direction == "top":
            args.append("--top")
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"kaku split-pane failed: {result.stderr}")

        pane_id = int(result.stdout.strip())
        win_id = self._find_pane_window(pane_id)

        pane = ManagedPane(
            pane_id=pane_id,
            window_id=win_id,
            purpose=purpose,
            agent_type=agent_type,
            state=PaneState.RUNNING,
        )
        self._managed[pane_id] = pane
        return pane

    # ── Pane I/O ───────────────────────────────────────────────────

    def send_text(self, pane_id: int, text: str, submit: bool = True) -> None:
        """Send text to a pane as paste, then optionally press Enter to submit.

        Args:
            pane_id: Target pane.
            text: Text to send.
            submit: If True, send a carriage return after the text to trigger
                    submission (e.g. in interactive CLIs like Claude Code).
        """
        content = text.rstrip("\n").rstrip("\r")
        result = subprocess.run(
            ["kaku", "cli", "send-text", "--pane-id", str(pane_id), "--", content],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"kaku send-text failed: {result.stderr}")

        if submit:
            # Wait for the CLI to finish processing the pasted text
            time.sleep(0.5)
            # Send raw carriage return byte via stdin to trigger Enter
            result = subprocess.run(
                ["kaku", "cli", "send-text", "--pane-id", str(pane_id), "--no-paste"],
                input="\r",
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"kaku send-text (enter) failed: {result.stderr}")

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        """Read text content from a pane."""
        args = ["kaku", "cli", "get-text", "--pane-id", str(pane_id)]
        if start_line is not None:
            args.extend(["--start-line", str(start_line)])
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"kaku get-text failed: {result.stderr}")
        return result.stdout

    def activate_pane(self, pane_id: int) -> None:
        """Focus a pane."""
        subprocess.run(
            ["kaku", "cli", "activate-pane", "--pane-id", str(pane_id)],
            capture_output=True, text=True,
        )

    def kill_pane(self, pane_id: int) -> None:
        """Kill a managed pane and remove it from tracking."""
        subprocess.run(
            ["kaku", "cli", "kill-pane", "--pane-id", str(pane_id)],
            capture_output=True, text=True,
        )
        self._managed.pop(pane_id, None)

    def set_title(self, pane_id: int, title: str) -> None:
        """Set the tab title for a pane."""
        subprocess.run(
            ["kaku", "cli", "set-tab-title", "--pane-id", str(pane_id), title],
            capture_output=True, text=True,
        )

    def set_window_title(self, pane_id: int, title: str) -> None:
        """Set the window title via a pane in that window."""
        subprocess.run(
            ["kaku", "cli", "set-window-title", "--pane-id", str(pane_id), title],
            capture_output=True, text=True,
        )

    # ── State management ───────────────────────────────────────────

    def update_state(self, pane_id: int, state: PaneState) -> None:
        if pane_id in self._managed:
            self._managed[pane_id].state = state

    def is_pane_alive(self, pane_id: int) -> bool:
        try:
            all_panes = self.list_all_panes()
            return any(p.pane_id == pane_id for p in all_panes)
        except RuntimeError:
            return False

    def refresh_states(self) -> None:
        """Check all managed panes and update states for dead ones."""
        try:
            alive_ids = {p.pane_id for p in self.list_all_panes()}
        except RuntimeError:
            return
        for pane_id, pane in list(self._managed.items()):
            if pane_id not in alive_ids:
                pane.state = PaneState.DONE

    def summary(self) -> dict:
        return {
            "total": len(self._managed),
            "windows": len(self._windows),
            "running": sum(1 for p in self._managed.values() if p.state == PaneState.RUNNING),
            "idle": sum(1 for p in self._managed.values() if p.state == PaneState.IDLE),
            "done": sum(1 for p in self._managed.values() if p.state == PaneState.DONE),
            "error": sum(1 for p in self._managed.values() if p.state == PaneState.ERROR),
            "panes": [
                {
                    "pane_id": p.pane_id,
                    "window_id": p.window_id,
                    "purpose": p.purpose,
                    "agent_type": p.agent_type,
                    "state": p.state.value,
                }
                for p in self._managed.values()
            ],
        }

    def cleanup_all(self) -> None:
        """Kill all managed panes and close managed windows."""
        for pane_id in list(self._managed):
            self.kill_pane(pane_id)
        self._windows.clear()

    # ── Context manager ────────────────────────────────────────────

    def __enter__(self) -> "PaneManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup_all()
        return None

    # ── Internal helpers ───────────────────────────────────────────

    def _find_pane_window(self, pane_id: int) -> int | None:
        """Find which window a pane belongs to."""
        try:
            for p in self.list_all_panes():
                if p.pane_id == pane_id:
                    return p.window_id
        except RuntimeError:
            pass
        return None
