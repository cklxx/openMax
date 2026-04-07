"""Task scheduler — picks tasks from queue and dispatches execution."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from openmax._paths import utc_now_iso
from openmax.server.queue import (
    SLOT_COST,
    QueuedTask,
    QueueStatus,
    SubtaskInfo,
    TaskQueue,
    TaskSize,
)
from openmax.server.sizer import estimate_task_size

if TYPE_CHECKING:
    from openmax.server.progress_bridge import ProgressBridge
    from openmax.server.ws_hub import WSHub

logger = logging.getLogger(__name__)


class Scheduler:
    """Priority scheduler that dispatches tasks from the queue."""

    def __init__(
        self,
        queue: TaskQueue,
        hub: WSHub,
        bridge: ProgressBridge,
        max_slots: int = 6,
    ) -> None:
        self._queue = queue
        self._hub = hub
        self._bridge = bridge
        self._max_slots = max_slots
        self._running = False
        self._active_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._running = True
        logger.info("Scheduler started (max_slots=%d)", self._max_slots)
        while self._running:
            await self._tick()
            await asyncio.sleep(2.0)

    def stop(self) -> None:
        self._running = False
        for t in self._active_tasks:
            t.cancel()

    async def _tick(self) -> None:
        await self._size_unsized_tasks()
        available = self._max_slots - self._queue.running_slot_cost()
        if available <= 0:
            return
        task = self._queue.next_runnable()
        if task is None:
            return
        cost = SLOT_COST.get(task.size, 2)
        if cost > available:
            return
        await self._dispatch(task)

    async def _size_unsized_tasks(self) -> None:
        """Size any queued tasks that haven't been sized yet."""
        for t in self._queue.list_all():
            if t.status != QueueStatus.QUEUED or t.size != TaskSize.UNKNOWN:
                continue
            if t.size_override:
                continue
            t.status = QueueStatus.SIZING
            await self._log_activity(t, "system", "Estimating task size...")
            estimate = await anyio.to_thread.run_sync(lambda: estimate_task_size(t.task, t.cwd))
            t.size = estimate.size
            t.size_confidence = estimate.confidence
            if not t.size_override:
                t.priority = estimate.suggested_priority
            t.status = QueueStatus.QUEUED
            await self._log_activity(
                t,
                "system",
                f"Sized as {t.size.value} (confidence: {t.size_confidence:.0%}). "
                f"{estimate.reasoning}",
            )

    async def _dispatch(self, task: QueuedTask) -> None:
        task.status = QueueStatus.RUNNING
        task.started_at = utc_now_iso()
        task.session_id = f"serve-{task.id}-{int(time.time())}"
        mode = "direct" if task.size == TaskSize.SMALL else "lead agent"
        await self._log_activity(task, "system", f"Dispatching via {mode}...")
        t = asyncio.create_task(self._execute(task))
        self._active_tasks.add(t)
        t.add_done_callback(self._active_tasks.discard)

    async def _execute(self, task: QueuedTask) -> None:
        """Run the task via lead agent or direct mode."""
        try:
            if task.size == TaskSize.SMALL:
                await self._run_direct(task)
            else:
                await self._run_lead_agent(task)
            task.status = QueueStatus.DONE
            task.finished_at = utc_now_iso()
            await self._log_activity(task, "system", "Task completed successfully")
        except Exception as exc:
            logger.error("Task %s failed: %s", task.id, exc)
            task.status = QueueStatus.ERROR
            task.error = traceback.format_exc()
            task.finished_at = utc_now_iso()
            await self._log_activity(task, "system", f"Error: {exc}", "error")
        finally:
            self._bridge.unwatch_session(task.id)
            self._queue.update(task)
            event = "task_completed" if task.status == QueueStatus.DONE else "task_error"
            await self._hub.broadcast(event, task.to_dict())

    async def _run_direct(self, task: QueuedTask) -> None:
        """Run a small task directly via claude -p."""
        await self._log_activity(task, "agent", "Running claude -p...")
        result = await anyio.to_thread.run_sync(lambda: _run_claude_direct(task.task, task.cwd))
        if result != 0:
            raise RuntimeError(f"Direct execution failed with exit code {result}")

    async def _run_lead_agent(self, task: QueuedTask) -> None:
        """Run a medium/large task through the lead agent."""
        from openmax.agent_registry import built_in_agent_registry
        from openmax.lead_agent import run_lead_agent
        from openmax.mailbox import SessionMailbox
        from openmax.pane_manager import PaneManager

        log_dir = Path(task.cwd) / ".openmax"
        log_dir.mkdir(parents=True, exist_ok=True)
        bridge = self._bridge
        task_id = task.id

        def on_message(msg):
            bridge.on_agent_message(task_id, msg)

        mailbox = SessionMailbox(task.session_id, log_dir, on_message=on_message)
        mailbox.start()
        self._bridge.register_task(task_id)
        await self._log_activity(task, "system", "Lead agent planning...")

        try:
            plan = await anyio.to_thread.run_sync(
                lambda: run_lead_agent(
                    task=task.task,
                    pane_mgr=PaneManager(),
                    cwd=task.cwd,
                    session_id=task.session_id,
                    agent_registry=built_in_agent_registry(),
                    plan_confirm=False,
                    mailbox=mailbox,
                    auto_retry=True,
                )
            )
            task.subtasks = [
                SubtaskInfo(name=st.name, status=st.status.value, agent_type=st.agent_type)
                for st in plan.subtasks
            ]
        finally:
            mailbox.stop()
            self._bridge.unregister_task(task_id)

    async def _log_activity(
        self, task: QueuedTask, source: str, message: str, entry_type: str = "info"
    ) -> None:
        entry = task.add_activity(source, message, entry_type)
        self._queue.update(task)
        await self._hub.broadcast(
            "activity",
            {
                "task_id": task.id,
                "entry": {
                    "timestamp": entry.timestamp,
                    "source": entry.source,
                    "message": entry.message,
                    "type": entry.type,
                },
            },
        )


def _run_claude_direct(prompt: str, cwd: str) -> int:
    """Run claude -p in a subprocess. Returns exit code."""
    import subprocess

    result = subprocess.run(
        ["claude", "-p", prompt],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode
