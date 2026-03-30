"""Starlette HTTP + WebSocket server for openMax dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from openmax.server.progress_bridge import ProgressBridge
from openmax.server.queue import QueueStatus, TaskQueue, TaskSize
from openmax.server.scheduler import Scheduler
from openmax.server.ws_hub import WSHub

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Module-level singletons, initialized in create_app()
_queue: TaskQueue | None = None
_hub: WSHub | None = None
_scheduler: Scheduler | None = None
_bridge: ProgressBridge | None = None


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def list_tasks(request: Request) -> JSONResponse:
    return JSONResponse([t.to_dict() for t in _queue.list_all()])


async def get_task(request: Request) -> JSONResponse:
    task = _queue.get(request.path_params["task_id"])
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(task.to_dict())


async def create_task(request: Request) -> JSONResponse:
    body = await request.json()
    text = body.get("task", "").strip()
    if not text:
        return JSONResponse({"error": "task is required"}, status_code=400)
    cwd = body.get("cwd", os.getcwd())
    task = _queue.add(text, cwd, body.get("priority", 50))
    await _hub.broadcast("task_created", task.to_dict())
    return JSONResponse(task.to_dict(), status_code=201)


async def update_task(request: Request) -> JSONResponse:
    task = _queue.get(request.path_params["task_id"])
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    if "priority" in body:
        task.priority = int(body["priority"])
    if "task" in body and body["task"].strip():
        task.task = body["task"].strip()
    if "size" in body:
        task.size = TaskSize(body["size"])
        task.size_override = True
    _queue.update(task)
    await _hub.broadcast("task_updated", task.to_dict())
    return JSONResponse(task.to_dict())


async def delete_task(request: Request) -> JSONResponse:
    task_id = request.path_params["task_id"]
    task = _queue.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if task.status == QueueStatus.RUNNING:
        task.status = QueueStatus.CANCELLED
        _queue.update(task)
    else:
        _queue.remove(task_id)
    await _hub.broadcast("task_cancelled", {"id": task_id})
    return JSONResponse({"ok": True})


async def stats(request: Request) -> JSONResponse:
    return JSONResponse(_queue.stats())


async def list_employees(request: Request) -> JSONResponse:
    from openmax.employees import list_employees as _list

    return JSONResponse(
        [
            {
                "name": e.name,
                "role": e.role,
                "specialty": e.specialty,
                "agent_type": e.agent_type,
                "task_count": e.task_count,
                "created": e.created,
            }
            for e in _list()
        ]
    )


async def ws_endpoint(ws: WebSocket) -> None:
    await _hub.handle(ws, _handle_ws_message)


async def _handle_ws_message(msg: dict[str, Any]) -> None:
    """Handle incoming WebSocket commands from the dashboard."""
    action = msg.get("action", "")
    if action == "submit_task":
        text = msg.get("task", "").strip()
        if text:
            cwd = msg.get("cwd", os.getcwd())
            task = _queue.add(text, cwd, msg.get("priority", 50))
            await _hub.broadcast("task_created", task.to_dict())
    elif action == "cancel_task":
        task = _queue.get(msg.get("task_id", ""))
        if task:
            task.status = QueueStatus.CANCELLED
            _queue.update(task)
            await _hub.broadcast("task_cancelled", task.to_dict())
    elif action == "update_priority":
        task = _queue.get(msg.get("task_id", ""))
        if task:
            task.priority = int(msg.get("priority", task.priority))
            _queue.update(task)
            await _hub.broadcast("task_updated", task.to_dict())


def _build_routes() -> list:
    return [
        Route("/health", health),
        Route("/api/tasks", list_tasks, methods=["GET"]),
        Route("/api/tasks", create_task, methods=["POST"]),
        Route("/api/tasks/{task_id}", get_task, methods=["GET"]),
        Route("/api/tasks/{task_id}", update_task, methods=["PATCH"]),
        Route("/api/tasks/{task_id}", delete_task, methods=["DELETE"]),
        Route("/api/stats", stats),
        Route("/api/employees", list_employees),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/", app=StaticFiles(directory=str(_STATIC_DIR), html=True)),
    ]


def create_app(queue_dir: Path | None = None, max_slots: int = 6) -> Starlette:
    """Create and configure the Starlette application."""
    global _queue, _hub, _scheduler, _bridge

    _queue = TaskQueue(queue_dir)
    _hub = WSHub()
    _bridge = ProgressBridge(_hub, _queue)
    _scheduler = Scheduler(_queue, _hub, _bridge, max_slots=max_slots)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        _bridge.set_loop(asyncio.get_running_loop())
        task = asyncio.create_task(_scheduler.start())
        logger.info("openMax server ready")
        yield
        _scheduler.stop()
        task.cancel()

    return Starlette(routes=_build_routes(), lifespan=lifespan)
