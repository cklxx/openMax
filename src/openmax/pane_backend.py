"""Execution backend abstraction for pane-based agent management."""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import unquote, urlparse

_KAKU_CLI_PREFIX = ["kaku", "cli"]


def _is_socket(path: str) -> bool:
    """Return True if *path* exists and is a Unix domain socket."""
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


_SEND_TEXT_ARG_LIMIT = 100_000  # bytes; switch to stdin above this

SplitDirection = Literal["right", "bottom", "left", "top"]
PaneBackendName = Literal["kaku", "kaku-tmux", "ghostty", "ghostty-tmux", "tmux", "headless"]
_VALID_BACKEND_NAMES = {"kaku", "kaku-tmux", "ghostty", "ghostty-tmux", "tmux", "headless"}


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

    Architecture: UI layer (terminal emulator) × backend layer (pane engine).
    Kaku/Ghostty are UI layers — they render windows. Tmux is the backend
    that manages pane grids with ``select-layout tiled``.

    When a UI terminal + tmux are both available, use the layered mode:
    UI opens a window → window attaches to a tmux session → tmux manages panes.

    macOS: kaku-tmux > ghostty-tmux > tmux > kaku fallback.
    Non-macOS: tmux.
    """
    from openmax.terminal import is_ghostty_available, is_kaku_available, is_tmux_available

    if platform.system() == "Darwin":
        has_tmux = is_tmux_available()
        if is_kaku_available() and has_tmux:
            return "kaku-tmux"
        if is_ghostty_available() and has_tmux:
            return "ghostty-tmux"
        if has_tmux:
            return "tmux"
        if is_kaku_available():
            return "kaku"
        if is_ghostty_available():
            return "ghostty"
        return "kaku"
    else:
        return "tmux"


def create_pane_backend(name: str | None = None) -> PaneBackend:
    """Create a pane backend instance for the selected backend name."""
    resolved = resolve_pane_backend_name(name)
    if resolved == "headless":
        return HeadlessPaneBackend()
    if resolved == "tmux":
        return TmuxPaneBackend()
    if resolved == "kaku-tmux":
        return LayeredPaneBackend("kaku")
    if resolved == "ghostty-tmux":
        return LayeredPaneBackend("ghostty")
    if resolved == "ghostty":
        return GhosttyPaneBackend()
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

    _socket_healed = False

    @classmethod
    def _heal_socket_symlink(cls) -> None:
        """Fix stale default socket symlink using KAKU_UNIX_SOCKET env var.

        Kaku doesn't update its default symlink on restart, causing all CLI
        commands to fail. We detect this by comparing the symlink target to
        the live socket path from the environment.
        """
        if cls._socket_healed:
            return
        cls._socket_healed = True
        live = os.environ.get("KAKU_UNIX_SOCKET")
        if not live or not _is_socket(live):
            return
        sock_dir = Path(live).parent
        default = sock_dir / "default-fun.tw93.kaku"
        try:
            if default.is_symlink() and str(default.resolve()) == str(Path(live).resolve()):
                return
            default.unlink(missing_ok=True)
            default.symlink_to(live)
        except OSError:
            pass

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
        return self._retry(lambda: self._spawn_window_once(command, cwd, env))

    def _spawn_window_once(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> int:
        args = ["spawn", "--new-window"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--")
        args.extend(_wrap_command_clean_env(command))
        result = self._run_kaku(args, env=env) if env else self._run_kaku(args)
        return int(result.stdout.strip())

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        return self._retry(
            lambda: self._split_pane_once(target_pane_id, direction, command, cwd, env)
        )

    def _split_pane_once(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
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
        result = self._run_kaku(args, env=env) if env else self._run_kaku(args)
        return int(result.stdout.strip())

    @staticmethod
    def _retry(fn, *, retries: int = 2, delay: float = 0.5):
        """Call fn(), retrying up to `retries` times on PaneBackendError."""
        for attempt in range(retries + 1):
            try:
                return fn()
            except PaneBackendError:
                if attempt >= retries:
                    raise
                time.sleep(delay)

    def send_text(self, pane_id: int, text: str) -> None:
        self._run_kaku(
            ["send-text", "--pane-id", str(pane_id), "--no-paste"],
            input_text=text,
        )

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

    @classmethod
    def _run_kaku(
        cls,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cls._heal_socket_symlink()
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


class GhosttyPaneBackend:
    """Ghostty-backed pane backend using AppleScript (macOS-only)."""

    _DIRECTION_MAP: dict[SplitDirection, str] = {
        "right": "right",
        "bottom": "down",
        "left": "left",
        "top": "up",
    }

    def list_panes(self) -> list[PaneInfo]:
        script = (
            'tell application "Ghostty"\n'
            "  set out to {}\n"
            "  repeat with w in windows\n"
            "    set wid to id of w\n"
            "    repeat with t in tabs of w\n"
            "      set tid to id of t\n"
            "      repeat with term in terminals of t\n"
            "        set pid to id of term\n"
            "        set tTitle to title of term\n"
            "        set tCwd to current directory of term\n"
            "        set tCols to columns of term\n"
            "        set tRows to rows of term\n"
            '        set end of out to ("" & wid & "\t" & tid & "\t"'
            ' & pid & "\t" & tTitle & "\t" & tCwd & "\t"'
            ' & tCols & "\t" & tRows)\n'
            "      end repeat\n"
            "    end repeat\n"
            "  end repeat\n"
            "  return (items of out) as text\n"
            "end tell"
        )
        result = self._run_applescript(script, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return self._parse_pane_list(result.stdout)

    @staticmethod
    def _parse_pane_list(raw: str) -> list[PaneInfo]:
        panes: list[PaneInfo] = []
        for line in raw.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            panes.append(
                PaneInfo(
                    window_id=int(parts[0]),
                    tab_id=int(parts[1]),
                    pane_id=int(parts[2]),
                    workspace="ghostty",
                    cols=int(parts[5]),
                    rows=int(parts[6]),
                    title=parts[3],
                    cwd=parts[4],
                    is_active=False,
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
        return self._retry(lambda: self._spawn_window_once(command, cwd, env))

    def _spawn_window_once(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> int:
        wrapped = _wrap_command_with_env(command, env)
        cmd_str = self._shell_join(wrapped)
        cfg_lines = [f'set command of cfg to "{self._escape(cmd_str)}"']
        if cwd:
            cfg_lines.append(f'set working directory of cfg to "{self._escape(cwd)}"')
        cfg_block = "\n    ".join(cfg_lines)
        script = (
            'tell application "Ghostty"\n'
            "  set cfg to new surface configuration\n"
            f"    {cfg_block}\n"
            "  set w to new window with configuration cfg\n"
            "  set tid to id of terminal 1 of tab 1 of w\n"
            "  return tid\n"
            "end tell"
        )
        result = self._run_applescript(script)
        return int(result.stdout.strip())

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        return self._retry(
            lambda: self._split_pane_once(target_pane_id, direction, command, cwd, env)
        )

    def _split_pane_once(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> int:
        ghostty_dir = self._DIRECTION_MAP[direction]
        wrapped = _wrap_command_with_env(command, env)
        cmd_str = self._shell_join(wrapped)
        cfg_lines = [f'set command of cfg to "{self._escape(cmd_str)}"']
        if cwd:
            cfg_lines.append(f'set working directory of cfg to "{self._escape(cwd)}"')
        cfg_block = "\n    ".join(cfg_lines)
        script = (
            'tell application "Ghostty"\n'
            "  set cfg to new surface configuration\n"
            f"    {cfg_block}\n"
            f"  set newTerm to split (terminal id {target_pane_id})"
            f" direction {ghostty_dir} with configuration cfg\n"
            "  return id of newTerm\n"
            "end tell"
        )
        result = self._run_applescript(script)
        return int(result.stdout.strip())

    def send_text(self, pane_id: int, text: str) -> None:
        escaped = self._escape(text)
        script = (
            'tell application "Ghostty"\n'
            f'  input text "{escaped}" to (terminal id {pane_id})\n'
            "end tell"
        )
        self._run_applescript(script)

    def send_enter(self, pane_id: int) -> None:
        script = (
            f'tell application "Ghostty"\n  send key "enter" to (terminal id {pane_id})\nend tell'
        )
        self._run_applescript(script)

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        # Save clipboard, use write_scrollback_file action, read via pbpaste
        save_clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        saved = save_clip.stdout
        try:
            action_script = (
                'tell application "Ghostty"\n'
                f'  perform action "write_scrollback_file:copy"'
                f" on (terminal id {pane_id})\n"
                "end tell"
            )
            self._run_applescript(action_script)
            time.sleep(0.1)
            clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            text = clip.stdout
        finally:
            subprocess.run(["pbcopy"], input=saved, text=True, timeout=5)
        if start_line is not None:
            lines = text.splitlines()
            return "\n".join(lines[start_line:])
        return text

    def activate_pane(self, pane_id: int) -> None:
        script = f'tell application "Ghostty"\n  focus (terminal id {pane_id})\nend tell'
        self._run_applescript(script, check=False)

    def set_window_title(self, pane_id: int, title: str) -> None:
        escaped = self._escape(title)
        script = (
            'tell application "Ghostty"\n'
            f"  set targetTerm to (terminal id {pane_id})\n"
            "  repeat with w in windows\n"
            "    repeat with t in tabs of w\n"
            "      repeat with term in terminals of t\n"
            "        if id of term = id of targetTerm then\n"
            f'          set name of w to "{escaped}"\n'
            "          return\n"
            "        end if\n"
            "      end repeat\n"
            "    end repeat\n"
            "  end repeat\n"
            "end tell"
        )
        self._run_applescript(script, check=False)

    def kill_pane(self, pane_id: int) -> None:
        script = f'tell application "Ghostty"\n  close (terminal id {pane_id})\nend tell'
        self._run_applescript(script, check=False)

    def resize_frontmost_window(self) -> None:
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
            '  tell process "Ghostty"\n'
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
    def _retry(fn, *, retries: int = 2, delay: float = 0.5):
        """Call fn(), retrying up to `retries` times on PaneBackendError."""
        for attempt in range(retries + 1):
            try:
                return fn()
            except PaneBackendError:
                if attempt >= retries:
                    raise
                time.sleep(delay)

    @staticmethod
    def _escape(text: str) -> str:
        """Escape a string for embedding in AppleScript double-quoted literals."""
        return text.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _shell_join(args: list[str]) -> str:
        """Join command args into a shell-safe string."""
        import shlex

        return shlex.join(args)

    @staticmethod
    def _run_applescript(
        script: str,
        *,
        check: bool = True,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise PaneBackendError(f"ghostty applescript failed: {result.stderr}")
        return result


_TMUX_SESSION_NAME = "openmax"


class TmuxPaneBackend:
    """Tmux-backed implementation of the pane execution backend.

    When running outside a tmux session, automatically creates a detached
    ``openmax`` session. Panes are visible via ``tmux attach -t openmax``.

    Args:
        socket_name: Optional tmux socket name (``-L`` flag) for session isolation.
            Primarily useful for testing.
        target_session: Optional session name for ``new-window``.
            Auto-detected when omitted.
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
        self._owns_session = False
        if target_session is not None:
            self._target_session = target_session
        elif os.environ.get("TMUX"):
            # Inside a tmux session — use current session
            self._target_session = None
        else:
            # Not inside tmux — create a detached session
            self._target_session = _TMUX_SESSION_NAME
            self._ensure_session()

    def _ensure_session(self) -> None:
        """Create a detached tmux session if it doesn't already exist."""
        result = self._run_tmux(
            ["has-session", "-t", self._target_session],
            check=False,
        )
        if result.returncode == 0:
            return  # session already exists
        self._run_tmux(
            [
                "new-session",
                "-d",
                "-s",
                self._target_session,
                "-x",
                "200",
                "-y",
                "50",
            ]
        )
        self._owns_session = True

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
        pane_id = _tmux_id(result.stdout.strip())
        self._run_tmux(["select-layout", "-t", f"%{pane_id}", "tiled"], check=False)
        return pane_id

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


def _launch_kaku_window(command: list[str]) -> None:
    """Open a Kaku window running the given command."""
    KakuPaneBackend._heal_socket_symlink()
    subprocess.run(
        [*_KAKU_CLI_PREFIX, "spawn", "--new-window", "--", *command],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _launch_ghostty_window(command: list[str]) -> None:
    """Open a Ghostty window running the given command."""
    import shlex

    cmd_str = shlex.join(command)
    escaped = cmd_str.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Ghostty"\n'
        "  set cfg to new surface configuration\n"
        f'    set command of cfg to "{escaped}"\n'
        "  set w to new window with configuration cfg\n"
        "end tell"
    )
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )


_UI_LAUNCHERS: dict[str, callable] = {
    "kaku": _launch_kaku_window,
    "ghostty": _launch_ghostty_window,
}


class LayeredPaneBackend:
    """UI terminal for window rendering, tmux for pane grid management.

    Kaku/Ghostty are UI layers (terminal emulators). Tmux is the backend
    that manages pane lifecycle and grid layout via ``select-layout tiled``.

    On first ``spawn_window``:
    1. Create a detached tmux session ``openmax``
    2. Open a UI terminal window attached to that session
    3. Run the first agent command in a tmux window

    Subsequent pane operations (split, send_text, etc.) go through tmux.
    """

    _RESIZE_SCRIPTS: dict[str, str] = {
        "kaku": (
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
        ),
        "ghostty": (
            'tell application "Finder"\n'
            "  set {_x, _y, sw, sh} to bounds of window of desktop\n"
            "end tell\n"
            "set w to round (sw * 0.5)\n"
            "set h to round (sh * 0.5)\n"
            "set xOff to round ((sw - w) / 2)\n"
            "set yOff to round ((sh - h) / 2)\n"
            'tell application "System Events"\n'
            '  tell process "Ghostty"\n'
            "    set position of window 1 to {xOff, yOff}\n"
            "    set size of window 1 to {w, h}\n"
            "  end tell\n"
            "end tell"
        ),
    }

    def __init__(self, ui: str) -> None:
        self._ui_launcher = _UI_LAUNCHERS[ui]
        self._tmux: TmuxPaneBackend | None = None
        self._session_ready = False
        self._ui_name = ui

    def _ensure_tmux(self) -> TmuxPaneBackend:
        if self._tmux is None:
            self._tmux = TmuxPaneBackend(target_session=_TMUX_SESSION_NAME)
        return self._tmux

    def _bootstrap_session(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> int:
        """Open UI window running tmux new-session with the first command.

        Single-step: the UI terminal launches ``tmux new-session`` directly
        so the session, first pane, and UI window are created atomically.
        No detach-then-attach race condition.
        """
        wrapped = _wrap_command_with_env(command, env)
        session_cmd = [
            "tmux",
            "new-session",
            "-s",
            _TMUX_SESSION_NAME,
            "-x",
            "200",
            "-y",
            "50",
        ]
        if cwd:
            session_cmd.extend(["-c", cwd])
        session_cmd.extend(wrapped)
        self._ui_launcher(session_cmd)

        self._session_ready = True
        time.sleep(0.8)
        return self._find_first_pane_id()

    @staticmethod
    def _find_first_pane_id() -> int:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", _TMUX_SESSION_NAME, "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise PaneBackendError(f"Cannot find panes in tmux session: {result.stderr}")
        return _tmux_id(result.stdout.strip().splitlines()[0])

    def spawn_window(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        if not self._session_ready:
            return self._bootstrap_session(command, cwd, env)
        return self._ensure_tmux().spawn_window(command, cwd=cwd, env=env)

    def split_pane(
        self,
        target_pane_id: int,
        direction: SplitDirection,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        return self._ensure_tmux().split_pane(
            target_pane_id,
            direction,
            command,
            cwd=cwd,
            env=env,
        )

    def list_panes(self) -> list[PaneInfo]:
        return self._ensure_tmux().list_panes()

    def send_text(self, pane_id: int, text: str) -> None:
        self._ensure_tmux().send_text(pane_id, text)

    def send_enter(self, pane_id: int) -> None:
        self._ensure_tmux().send_enter(pane_id)

    def get_text(self, pane_id: int, start_line: int | None = None) -> str:
        return self._ensure_tmux().get_text(pane_id, start_line=start_line)

    def activate_pane(self, pane_id: int) -> None:
        self._ensure_tmux().activate_pane(pane_id)

    def set_window_title(self, pane_id: int, title: str) -> None:
        self._ensure_tmux().set_window_title(pane_id, title)

    def kill_pane(self, pane_id: int) -> None:
        self._ensure_tmux().kill_pane(pane_id)

    def resize_frontmost_window(self) -> None:
        if platform.system() != "Darwin":
            return
        script = self._RESIZE_SCRIPTS.get(self._ui_name)
        if script:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )


def _tmux_id(raw: str) -> int:
    """Parse a tmux ID like '%3' or '@1' to an integer."""
    cleaned = raw.lstrip("%@$")
    return int(cleaned)
