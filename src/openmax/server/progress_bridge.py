"""Bridge between SessionMailbox events and WebSocket hub."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import anyio

from openmax.mailbox import MailboxMessage, SessionMailbox

if TYPE_CHECKING:
    from openmax.server.ws_hub import WSHub

logger = logging.getLogger(__name__)


class ProgressBridge:
    """Forwards mailbox messages to WebSocket clients in real-time."""

    def __init__(self, ws_hub: WSHub) -> None:
        self._hub = ws_hub
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
                await self._hub.broadcast(
                    "subtask_progress",
                    {
                        "task_id": task_id,
                        "subtask": msg.task,
                        "type": msg.type,
                        "data": msg.raw,
                    },
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Progress bridge error for %s: %s", task_id, exc)
