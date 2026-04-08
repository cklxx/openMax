"""Mailbox IPC module."""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SOCKET_DIR = Path("/tmp")
_MAX_MSG_BYTES = 64_000
_SOCKET_TIMEOUT_SECONDS = 5.0


def mailbox_socket_path(session_id: str) -> Path:
    return _SOCKET_DIR / f"openmax-{session_id}.sock"


def send_mailbox_message(session_id: str, message: str) -> None:
    sock_path = mailbox_socket_path(session_id)
    if not sock_path.exists():
        raise FileNotFoundError(f"no active session socket: {sock_path}")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_SOCKET_TIMEOUT_SECONDS)
        sock.connect(str(sock_path))
        sock.sendall(message.encode("utf-8"))


def send_mailbox_payload(session_id: str, payload: dict[str, Any]) -> None:
    send_mailbox_message(session_id, json.dumps(payload, ensure_ascii=False))


@dataclass
class MailboxMessage:
    type: str
    task: str
    raw: dict[str, Any]
    received_at: float


class SessionMailbox:
    def __init__(
        self,
        session_id: str,
        log_dir: Path,
        on_message: Callable[[MailboxMessage], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.socket_path = mailbox_socket_path(session_id)
        self.log_path = log_dir / f"messages-{session_id}.jsonl"
        self._queue: queue.Queue[MailboxMessage] = queue.Queue()
        self._server_sock: socket.socket | None = None
        self._stop = threading.Event()
        self._on_message = on_message

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(str(self.socket_path))
        self._server_sock.listen(64)
        self._server_sock.settimeout(1.0)
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._server_sock:
                self._server_sock.close()
        except OSError:
            pass
        self.socket_path.unlink(missing_ok=True)

    def receive(self, timeout: float = 30.0) -> MailboxMessage | None:
        """Blocking receive — called via anyio.to_thread.run_sync."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server_sock.accept()
            except TimeoutError:
                continue  # poll _stop and retry
            except OSError:
                if not self._stop.is_set():
                    logger.warning("Mailbox server socket error, shutting down")
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        try:
            conn.settimeout(5.0)
            while len(buf) < _MAX_MSG_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            logger.debug("Mailbox connection recv failed", exc_info=True)
        finally:
            conn.close()
        if not buf:
            return
        try:
            raw = json.loads(buf.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Malformed mailbox message discarded (len=%d)", len(buf))
            return
        if not isinstance(raw, dict) or "type" not in raw:
            return
        entry = {**raw, "_ts": time.time()}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        msg = MailboxMessage(
            type=raw["type"],
            task=raw.get("task", ""),
            raw=raw,
            received_at=time.time(),
        )
        self._queue.put_nowait(msg)
        if self._on_message:
            try:
                self._on_message(msg)
            except Exception:
                logger.exception("Mailbox on_message handler raised")
