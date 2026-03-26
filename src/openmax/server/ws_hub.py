"""WebSocket connection hub — broadcast task events to all connected clients."""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WSHub:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info("WebSocket connected (%d total)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info("WebSocket disconnected (%d total)", len(self._connections))

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    async def handle(self, ws: WebSocket, on_message: Any) -> None:
        """Run the receive loop for a single WebSocket client."""
        await self.connect(ws)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if on_message:
                    await on_message(msg)
        except WebSocketDisconnect:
            pass
        finally:
            self.disconnect(ws)
