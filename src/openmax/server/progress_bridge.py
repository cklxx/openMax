"""Bridge between SessionMailbox events and WebSocket hub."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from openmax.mailbox import MailboxMessage

if TYPE_CHECKING:
    from openmax.server.queue import TaskQueue
    from openmax.server.ws_hub import WSHub

logger = logging.getLogger(__name__)


class ProgressBridge:
    """Forwards mailbox messages to WebSocket clients via on_message callback."""

    def __init__(self, ws_hub: WSHub, queue: TaskQueue) -> None:
        self._hub = ws_hub
        self._queue = queue
        self._active_tasks: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_task(self, task_id: str) -> None:
        self._active_tasks.add(task_id)

    def unregister_task(self, task_id: str) -> None:
        self._active_tasks.discard(task_id)

    # Legacy compatibility — scheduler.py calls this in finally block
    def unwatch_session(self, task_id: str) -> None:
        self.unregister_task(task_id)

    def on_agent_message(self, task_id: str, msg: MailboxMessage) -> None:
        """Called from mailbox thread when a sub-agent sends a message."""
        if task_id not in self._active_tasks:
            return
        loop = self._loop or _get_loop()
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(asyncio.ensure_future, self._forward(task_id, msg))

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


def _get_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return None


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
