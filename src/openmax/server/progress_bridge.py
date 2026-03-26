"""Bridge between SessionMailbox events and WebSocket hub."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import anyio

from openmax.mailbox import MailboxMessage, SessionMailbox

if TYPE_CHECKING:
    from openmax.server.queue import TaskQueue
    from openmax.server.ws_hub import WSHub

logger = logging.getLogger(__name__)


class ProgressBridge:
    """Forwards mailbox messages to WebSocket clients in real-time."""

    def __init__(self, ws_hub: WSHub, queue: TaskQueue) -> None:
        self._hub = ws_hub
        self._queue = queue
        self._watches: dict[str, asyncio.Task[None]] = {}

    def watch_session(self, task_id: str, mailbox: SessionMailbox) -> None:
        """Start forwarding messages from a session mailbox."""
        if task_id in self._watches:
            return
        loop = asyncio.get_event_loop()
        self._watches[task_id] = loop.create_task(self._poll(task_id, mailbox))

    def unwatch_session(self, task_id: str) -> None:
        t = self._watches.pop(task_id, None)
        if t:
            t.cancel()

    async def _poll(self, task_id: str, mailbox: SessionMailbox) -> None:
        """Poll mailbox in a thread and forward to WebSocket hub."""
        try:
            while True:
                msg: MailboxMessage | None = await anyio.to_thread.run_sync(
                    lambda: mailbox.receive(timeout=5.0)
                )
                if msg is None:
                    continue
                await self._forward(task_id, msg)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Progress bridge error for %s: %s", task_id, exc)

    async def _forward(self, task_id: str, msg: MailboxMessage) -> None:
        message = _extract_message(msg)
        task = self._queue.get(task_id)
        if task:
            entry = task.add_activity(msg.task or "agent", message, msg.type)
            self._queue.update(task)
            await self._hub.broadcast(
                "activity",
                {
                    "task_id": task_id,
                    "entry": {
                        "timestamp": entry.timestamp,
                        "source": entry.source,
                        "message": entry.message,
                        "type": entry.type,
                    },
                },
            )
        await self._hub.broadcast(
            "subtask_progress",
            {
                "task_id": task_id,
                "subtask": msg.task,
                "type": msg.type,
                "data": msg.raw,
            },
        )


def _extract_message(msg: MailboxMessage) -> str:
    """Extract a human-readable message from a mailbox payload."""
    raw = msg.raw
    if "message" in raw:
        return str(raw["message"])
    if "summary" in raw:
        return str(raw["summary"])
    if msg.type == "done":
        return f"Completed: {msg.task}"
    if msg.type == "progress":
        pct = raw.get("progress_pct", "")
        return f"Progress: {pct}%" if pct else "Working..."
    return msg.type
