"""Execution backend abstraction for pane-based agent management."""

from __future__ import annotations

import json
import platform
import subprocess
import threading
from dataclasses import dataclass, field
from itertools import count
from typing import Literal, Protocol
from urllib.parse import unquote, urlparse

_KAKU_CLI_PREFIX = ["kaku", "cli"]

SplitDirection = Literal["right", "bottom", "left", "top"]


class PaneBackendError(RuntimeError):
    """Stable error raised by pane backends."""


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


@dataclass
class _HeadlessWorker:
    pane_id: int
    window_id: int
    process: subprocess.Popen[str]
    cwd: str
    title: str = ""
    output_chunks: list[str] = field(default_factory=list)
    output_lock: threading.Lock = field(default_factory=threading.Lock)


def _wrap_command_clean_env(command: list[str]) -> list[str]:
    """Wrap a command to run without Claude Code env vars leaking in."""
    return ["env", "-u", "CLAUDECODE", "-u", "CLAUDE_CODE_ENTRYPOINT"] + command


class HeadlessPaneBackend:
    """Subprocess-backed pane backend for tests and CI."""

    def __init__(self) -> None:
        self._pane_ids = count(1)
        self._window_ids = count(1)
        self._workers: dict[int, _HeadlessWorker] = {}
        self._active_pane_id: int | None = None

    def list_panes(self) -> list[PaneInfo]:
        panes: list[PaneInfo] = []
        for worker in self._workers.values():
            if worker.process.poll() is not None:
                continue
            panes.append(
                PaneInfo(
                    window_id=worker.window_id,
                    tab_id=1,
                    pane_id=worker.pane_id,
                    workspace="headless",
                    rows=24,
                    cols=80,
                    title=worker.title,
                    cwd=worker.cwd,
                    is_active=worker.pane_id == self._active_pane_id,
                    is_zoomed=False,
                    cursor_visibility="visible",
                )
            )
        return panes

    def spawn_window(self, command: list[str], cwd: str | None = None) -> int:
        pane_id = next(self._pane_ids)
        window_id = next(self._window_ids)
        self._workers[pane_id] = self._start_worker(
            pane_id=pane_id,
            window_id=window_id,
            command=command,
            cwd=cwd,
        )
        self._active_pane_id = pane_id
        return pane_id

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
    ) -> int:
        del direction
        target = self._require_worker(target_pane_id)
        pane_id = next(self._pane_ids)
        self._workers[pane_id] = self._start_worker(
            pane_id=pane_id,
            window_id=target.window_id,
            command=command,
            cwd=cwd,
        )
        self._active_pane_id = pane_id
        return pane_id

    def send_text(self, pane_id: int, text: str) -> None:
        self._write_stdin(pane_id, text)

    def send_enter(self, pane_id: int) -> None:
        self._write_stdin(pane_id, "\n")

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        worker = self._require_worker(pane_id)
        with worker.output_lock:
            text = "".join(worker.output_chunks)
        if start_line is None:
            return text
        lines = text.splitlines()
        return "\n".join(lines[start_line:])

    def activate_pane(self, pane_id: int) -> None:
        self._require_worker(pane_id)
        self._active_pane_id = pane_id

    def set_window_title(self, pane_id: int, title: str) -> None:
        worker = self._require_worker(pane_id)
        for candidate in self._workers.values():
            if candidate.window_id == worker.window_id:
                candidate.title = title

    def kill_pane(self, pane_id: int) -> None:
        worker = self._require_worker(pane_id)
        if worker.process.poll() is None:
            worker.process.terminate()
            try:
                worker.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait(timeout=1)
        self._workers.pop(pane_id, None)
        if self._active_pane_id == pane_id:
            self._active_pane_id = None

    def resize_frontmost_window(self) -> None:
        return None

    def _require_worker(self, pane_id: int) -> _HeadlessWorker:
        worker = self._workers.get(pane_id)
        if worker is None:
            raise PaneBackendError(f"unknown pane: {pane_id}")
        return worker

    def _start_worker(
        self,
        *,
        pane_id: int,
        window_id: int,
        command: list[str],
        cwd: str | None,
    ) -> _HeadlessWorker:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise PaneBackendError("headless pane spawn failed") from exc

        if process.stdin is None or process.stdout is None:
            process.kill()
            process.wait(timeout=1)
            raise PaneBackendError("headless pane spawn failed")

        worker = _HeadlessWorker(
            pane_id=pane_id,
            window_id=window_id,
            process=process,
            cwd=cwd or "",
        )
        thread = threading.Thread(
            target=self._capture_output,
            args=(worker,),
            daemon=True,
        )
        thread.start()
        return worker

    def _write_stdin(self, pane_id: int, data: str) -> None:
        worker = self._require_worker(pane_id)
        if worker.process.poll() is not None or worker.process.stdin is None:
            raise PaneBackendError(f"pane {pane_id} is not accepting input")
        try:
            worker.process.stdin.write(data)
            worker.process.stdin.flush()
        except OSError as exc:
            raise PaneBackendError(f"pane {pane_id} is not accepting input") from exc

    @staticmethod
    def _capture_output(worker: _HeadlessWorker) -> None:
        if worker.process.stdout is None:
            return
        while True:
            chunk = worker.process.stdout.read(1)
            if chunk == "":
                break
            with worker.output_lock:
                worker.output_chunks.append(chunk)


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
            raise PaneBackendError(f"kaku {command_name} failed: {result.stderr}")
        return result
