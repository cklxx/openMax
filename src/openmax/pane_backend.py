"""Execution backend abstraction for pane-based agent management."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
from dataclasses import dataclass, field
from itertools import count
from typing import Literal, Protocol, cast
from urllib.parse import unquote, urlparse

_KAKU_CLI_PREFIX = ["kaku", "cli"]
_SEND_TEXT_ARG_LIMIT = 100_000  # bytes; switch to stdin above this

SplitDirection = Literal["right", "bottom", "left", "top"]
PaneBackendName = Literal["kaku", "tmux", "headless"]
_VALID_BACKEND_NAMES = {"kaku", "tmux", "headless"}


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

    def spawn_window(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int: ...

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int: ...

    def send_text(self, pane_id: int, text: str) -> None: ...

    def send_enter(self, pane_id: int) -> None: ...

    def get_text(self, pane_id: int, start_line: int | None = None) -> str: ...

    def activate_pane(self, pane_id: int) -> None: ...

    def set_window_title(self, pane_id: int, title: str) -> None: ...

    def kill_pane(self, pane_id: int) -> None: ...

    def resize_frontmost_window(self) -> None: ...


def resolve_pane_backend_name(name: str | None = None) -> PaneBackendName:
    """Resolve a pane backend name from an explicit value or environment."""
    raw_value = name if name is not None else os.environ.get("OPENMAX_PANE_BACKEND", "auto")
    normalized = raw_value.strip().lower()
    if normalized == "auto":
        return _auto_detect_backend()
    if normalized not in _VALID_BACKEND_NAMES:
        raise ValueError(f"Unknown pane backend: {raw_value}")
    return cast(PaneBackendName, normalized)


def _auto_detect_backend() -> PaneBackendName:
    """Auto-detect the best available pane backend.

    macOS: kaku > tmux.  Non-macOS: tmux > kaku.
    """
    from openmax.terminal import is_kaku_available, is_tmux_available

    if platform.system() == "Darwin":
        if is_kaku_available():
            return "kaku"
        if is_tmux_available():
            return "tmux"
        return "kaku"  # fall through — ensure_kaku will guide install
    else:
        if is_tmux_available():
            return "tmux"
        return "tmux"  # fall through — ensure_tmux will guide install


def create_pane_backend(name: str | None = None) -> PaneBackend:
    """Create a pane backend instance for the selected backend name."""
    resolved = resolve_pane_backend_name(name)
    if resolved == "headless":
        return HeadlessPaneBackend()
    if resolved == "tmux":
        return TmuxPaneBackend()
    return KakuPaneBackend()


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


def _wrap_command_with_env(command: list[str], env: dict[str, str] | None) -> list[str]:
    """Wrap a command with ``env`` to set extra vars and unset Claude Code vars.

    Tmux panes inherit the tmux *server* environment, not the client's.
    So env vars must be baked into the command via ``env K=V … cmd``.
    """
    env_prefix = ["env", "-u", "CLAUDECODE", "-u", "CLAUDE_CODE_ENTRYPOINT"]
    if env:
        env_prefix.extend(f"{k}={v}" for k, v in env.items())
    return env_prefix + command


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

    def spawn_window(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        pane_id = next(self._pane_ids)
        window_id = next(self._window_ids)
        self._workers[pane_id] = self._start_worker(
            pane_id=pane_id,
            window_id=window_id,
            command=command,
            cwd=cwd,
            env=env,
        )
        self._active_pane_id = pane_id
        return pane_id

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        del direction
        target = self._require_worker(target_pane_id)
        pane_id = next(self._pane_ids)
        self._workers[pane_id] = self._start_worker(
            pane_id=pane_id,
            window_id=target.window_id,
            command=command,
            cwd=cwd,
            env=env,
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
        env: dict[str, str] | None,
    ) -> _HeadlessWorker:
        process_env = dict(os.environ)
        if env:
            process_env.update(env)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=process_env,
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

    def spawn_window(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        args = ["spawn", "--new-window"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))
        if env:
            result = self._run_kaku(args, env=env)
        else:
            result = self._run_kaku(args)
        return int(result.stdout.strip())

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
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
        if env:
            result = self._run_kaku(args, env=env)
        else:
            result = self._run_kaku(args)
        return int(result.stdout.strip())

    def send_text(self, pane_id: int, text: str) -> None:
        if len(text) > _SEND_TEXT_ARG_LIMIT:
            self._run_kaku(
                ["send-text", "--pane-id", str(pane_id), "--no-paste"],
                input_text=text,
            )
        else:
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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        result = subprocess.run(
            [*_KAKU_CLI_PREFIX, *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
        if check and result.returncode != 0:
            command_name = " ".join(args[:2]).strip()
            raise PaneBackendError(f"kaku {command_name} failed: {result.stderr}")
        return result


class TmuxPaneBackend:
    """Tmux-backed implementation of the pane execution backend.

    Args:
        socket_name: Optional tmux socket name (``-L`` flag) for session isolation.
            When set, all commands target this specific tmux server instance.
            Primarily useful for testing.
        target_session: Optional session name for ``new-window`` when not running
            inside a tmux session (no ``$TMUX`` env var).
    """

    _DIRECTION_FLAGS: dict[SplitDirection, list[str]] = {
        "right": ["-h"],
        "bottom": [],
        "left": ["-h", "-b"],
        "top": ["-b"],
    }

    def __init__(
        self,
        socket_name: str | None = None,
        target_session: str | None = None,
    ) -> None:
        self._socket_name = socket_name
        self._target_session = target_session

    def list_panes(self) -> list[PaneInfo]:
        fmt = (
            "#{window_id}\t#{pane_id}\t#{pane_width}\t#{pane_height}"
            "\t#{pane_title}\t#{pane_current_path}\t#{pane_active}"
            "\t#{window_zoomed_flag}"
        )
        result = self._run_tmux(["list-panes", "-a", "-F", fmt], check=False)
        if result.returncode != 0:
            return []
        panes: list[PaneInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            panes.append(
                PaneInfo(
                    window_id=_tmux_id(parts[0]),
                    tab_id=1,
                    pane_id=_tmux_id(parts[1]),
                    workspace="tmux",
                    cols=int(parts[2]),
                    rows=int(parts[3]),
                    title=parts[4],
                    cwd=parts[5],
                    is_active=parts[6] == "1",
                    is_zoomed=parts[7] == "1",
                    cursor_visibility="visible",
                )
            )
        return panes

    def spawn_window(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        args = ["new-window", "-P", "-F", "#{pane_id}"]
        if self._target_session:
            args.extend(["-t", f"{self._target_session}:"])
        if cwd:
            args.extend(["-c", cwd])
        args.extend(_wrap_command_with_env(command, env))
        result = self._run_tmux(args)
        return _tmux_id(result.stdout.strip())

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        args = ["split-window", "-t", f"%{target_pane_id}"]
        args.extend(self._DIRECTION_FLAGS[direction])
        args.extend(["-P", "-F", "#{pane_id}"])
        if cwd:
            args.extend(["-c", cwd])
        args.extend(_wrap_command_with_env(command, env))
        result = self._run_tmux(args)
        return _tmux_id(result.stdout.strip())

    def send_text(self, pane_id: int, text: str) -> None:
        if len(text.encode()) > _SEND_TEXT_ARG_LIMIT:
            # Large text: pipe through load-buffer then paste into pane
            self._run_tmux(["load-buffer", "-"], input_text=text)
            self._run_tmux(["paste-buffer", "-d", "-t", f"%{pane_id}"])
        else:
            self._run_tmux(["send-keys", "-t", f"%{pane_id}", "-l", text])

    def send_enter(self, pane_id: int) -> None:
        self._run_tmux(["send-keys", "-t", f"%{pane_id}", "Enter"])

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        # Capture entire scrollback history
        result = self._run_tmux(
            ["capture-pane", "-t", f"%{pane_id}", "-p", "-S", "-"],
            check=False,
        )
        if result.returncode != 0:
            return ""
        text = result.stdout
        if start_line is not None:
            lines = text.splitlines()
            return "\n".join(lines[start_line:])
        return text

    def activate_pane(self, pane_id: int) -> None:
        self._run_tmux(["select-pane", "-t", f"%{pane_id}"], check=False)

    def set_window_title(self, pane_id: int, title: str) -> None:
        # Find the window for this pane, then rename it
        result = self._run_tmux(
            ["display-message", "-t", f"%{pane_id}", "-p", "#{window_id}"],
            check=False,
        )
        if result.returncode != 0:
            return
        window_id = result.stdout.strip()
        self._run_tmux(["rename-window", "-t", window_id, title], check=False)

    def kill_pane(self, pane_id: int) -> None:
        self._run_tmux(["kill-pane", "-t", f"%{pane_id}"], check=False)

    def resize_frontmost_window(self) -> None:
        # tmux runs inside a terminal — window resizing is not applicable
        return None

    def _run_tmux(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        cmd = ["tmux"]
        if self._socket_name:
            cmd.extend(["-L", self._socket_name])
        cmd.extend(args)
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
        if check and result.returncode != 0:
            command_name = args[0] if args else "tmux"
            raise PaneBackendError(f"tmux {command_name} failed: {result.stderr}")
        return result


def _tmux_id(raw: str) -> int:
    """Parse a tmux ID like '%3' or '@1' to an integer."""
    cleaned = raw.lstrip("%@$")
    return int(cleaned)
