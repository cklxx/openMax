"""Execution backend abstraction for pane-based agent management."""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import unquote, urlparse

_KAKU_CLI_PREFIX = ["kaku", "cli"]

SplitDirection = Literal["right", "bottom", "left", "top"]


@dataclass
class PaneInfo:
    """Raw pane info exposed by the execution backend."""

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


class PaneBackend(Protocol):
    """Backend operations required by ``PaneManager``."""

    def list_panes(self) -> list[PaneInfo]: ...

    def spawn_window(self, command: list[str], cwd: str | None = None) -> int: ...

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
    ) -> int: ...

    def send_text(self, pane_id: int, text: str) -> None: ...

    def send_enter(self, pane_id: int) -> None: ...

    def get_text(self, pane_id: int, start_line: int | None = None) -> str: ...

    def activate_pane(self, pane_id: int) -> None: ...

    def set_window_title(self, pane_id: int, title: str) -> None: ...

    def kill_pane(self, pane_id: int) -> None: ...

    def resize_frontmost_window(self) -> None: ...


def _wrap_command_clean_env(command: list[str]) -> list[str]:
    """Wrap a command to run without Claude Code env vars leaking in."""
    return ["env", "-u", "CLAUDECODE", "-u", "CLAUDE_CODE_ENTRYPOINT"] + command


class KakuPaneBackend:
    """Kaku-backed implementation of the pane execution backend."""

    def list_panes(self) -> list[PaneInfo]:
        result = self._run_kaku(["list", "--format", "json"])
        raw = json.loads(result.stdout)
        panes = []
        for pane in raw:
            cwd = pane.get("cwd", "")
            if cwd.startswith("file://"):
                cwd = unquote(urlparse(cwd).path)
            panes.append(
                PaneInfo(
                    window_id=pane["window_id"],
                    tab_id=pane["tab_id"],
                    pane_id=pane["pane_id"],
                    workspace=pane.get("workspace", ""),
                    rows=pane["size"]["rows"],
                    cols=pane["size"]["cols"],
                    title=pane.get("title", ""),
                    cwd=cwd,
                    is_active=pane.get("is_active", False),
                    is_zoomed=pane.get("is_zoomed", False),
                    cursor_visibility=pane.get("cursor_visibility", ""),
                )
            )
        return panes

    def spawn_window(self, command: list[str], cwd: str | None = None) -> int:
        args = ["spawn", "--new-window"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))
        result = self._run_kaku(args)
        return int(result.stdout.strip())

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
    ) -> int:
        args = ["split-pane", "--pane-id", str(target_pane_id)]
        direction_flag = {
            "right": "--right",
            "bottom": "--bottom",
            "left": "--left",
            "top": "--top",
        }[direction]
        args.append(direction_flag)
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))
        result = self._run_kaku(args)
        return int(result.stdout.strip())

    def send_text(self, pane_id: int, text: str) -> None:
        self._run_kaku(["send-text", "--pane-id", str(pane_id), "--", text])

    def send_enter(self, pane_id: int) -> None:
        self._run_kaku(
            ["send-text", "--pane-id", str(pane_id), "--no-paste"],
            input_text="\r",
        )

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        args = ["get-text", "--pane-id", str(pane_id)]
        if start_line is not None:
            args.extend(["--start-line", str(start_line)])
        result = self._run_kaku(args)
        return result.stdout

    def activate_pane(self, pane_id: int) -> None:
        self._run_kaku(["activate-pane", "--pane-id", str(pane_id)], check=False)

    def set_window_title(self, pane_id: int, title: str) -> None:
        self._run_kaku(
            ["set-window-title", "--pane-id", str(pane_id), title],
            check=False,
        )

    def kill_pane(self, pane_id: int) -> None:
        self._run_kaku(["kill-pane", "--pane-id", str(pane_id)], check=False)

    def resize_frontmost_window(self) -> None:
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
