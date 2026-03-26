"""Tests for the task scheduler — state transitions, slot accounting, progress bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openmax.server.progress_bridge import ProgressBridge, _extract_message
from openmax.server.queue import QueueStatus, TaskQueue, TaskSize
from openmax.server.scheduler import Scheduler


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "queue")


@pytest.fixture
def hub():
    h = MagicMock()
    h.broadcast = AsyncMock()
    return h


@pytest.fixture
def bridge(hub, queue, event_loop):
    b = ProgressBridge(hub, queue)
    b.set_loop(event_loop)
    return b


@pytest.fixture
def scheduler(queue, hub, bridge):
    return Scheduler(queue, hub, bridge, max_slots=6)


# ── Slot cost accounting ──


def test_slot_cost_empty(queue):
    assert queue.running_slot_cost() == 0


def test_slot_cost_mixed(queue):
    t1 = queue.add("small task", "/tmp")
    t1.size = TaskSize.SMALL
    t1.status = QueueStatus.RUNNING
    queue.update(t1)

    t2 = queue.add("large task", "/tmp")
    t2.size = TaskSize.LARGE
    t2.status = QueueStatus.RUNNING
    queue.update(t2)

    assert queue.running_slot_cost() == 4  # 1 + 3


# ── Next runnable ──


def test_next_runnable_skips_unsized(queue):
    t = queue.add("unsized", "/tmp")
    assert t.size == TaskSize.UNKNOWN
    assert queue.next_runnable() is None


def test_next_runnable_picks_sized_queued(queue):
    t = queue.add("sized", "/tmp")
    t.size = TaskSize.SMALL
    queue.update(t)
    result = queue.next_runnable()
    assert result is not None
    assert result.id == t.id


def test_next_runnable_skips_running(queue):
    t = queue.add("running", "/tmp")
    t.size = TaskSize.SMALL
    t.status = QueueStatus.RUNNING
    queue.update(t)
    assert queue.next_runnable() is None


# ── Scheduler tick ──


@pytest.mark.asyncio
async def test_tick_no_tasks(tmp_path):
    """tick() with empty queue does nothing."""
    q = TaskQueue(tmp_path / "q")
    h = MagicMock()
    h.broadcast = AsyncMock()
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())
    s = Scheduler(q, h, b, max_slots=6)
    await s._tick()
    h.broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_tick_skips_when_full(tmp_path):
    """tick() skips dispatch when slots are full."""
    q = TaskQueue(tmp_path / "q")
    h = MagicMock()
    h.broadcast = AsyncMock()
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())
    s = Scheduler(q, h, b, max_slots=6)

    for i in range(3):
        t = q.add(f"task-{i}", "/tmp")
        t.size = TaskSize.LARGE
        t.status = QueueStatus.RUNNING
        q.update(t)
    waiting = q.add("waiting", "/tmp")
    waiting.size = TaskSize.SMALL
    q.update(waiting)

    await s._tick()
    assert waiting.status == QueueStatus.QUEUED


@pytest.mark.asyncio
async def test_dispatch_sets_running(tmp_path):
    q = TaskQueue(tmp_path / "q")
    h = MagicMock()
    h.broadcast = AsyncMock()
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())
    s = Scheduler(q, h, b, max_slots=6)

    t = q.add("dispatch me", "/tmp")
    t.size = TaskSize.SMALL
    q.update(t)

    await s._dispatch(t)
    assert t.status == QueueStatus.RUNNING
    assert t.started_at is not None
    assert t.session_id is not None
    assert len(s._active_tasks) == 1


# ── Progress Bridge ──


def test_extract_message_done():
    msg = MagicMock()
    msg.type = "done"
    msg.task = "my_task"
    msg.raw = {}
    assert _extract_message(msg) == "Completed: my_task"


def test_extract_message_with_message_field():
    msg = MagicMock()
    msg.type = "progress"
    msg.task = "t"
    msg.raw = {"message": "Building module"}
    assert _extract_message(msg) == "Building module"


def test_extract_message_progress_pct():
    msg = MagicMock()
    msg.type = "progress"
    msg.task = "t"
    msg.raw = {"progress_pct": 42}
    assert _extract_message(msg) == "Progress: 42%"


@pytest.mark.asyncio
async def test_bridge_register_unregister(tmp_path):
    h = MagicMock()
    h.broadcast = AsyncMock()
    q = TaskQueue(tmp_path / "q")
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())
    b.register_task("t1")
    assert "t1" in b._active_tasks
    b.unwatch_session("t1")
    assert "t1" not in b._active_tasks


@pytest.mark.asyncio
async def test_bridge_forward_broadcasts(tmp_path):
    h = MagicMock()
    h.broadcast = AsyncMock()
    q = TaskQueue(tmp_path / "q")
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())

    t = q.add("test", "/tmp")
    b.register_task(t.id)

    msg = MagicMock()
    msg.type = "progress"
    msg.task = "subtask_1"
    msg.raw = {"message": "compiling"}

    await b._forward(t.id, msg)
    assert h.broadcast.call_count == 2
    calls = [c[0][0] for c in h.broadcast.call_args_list]
    assert "activity" in calls
    assert "subtask_progress" in calls


@pytest.mark.asyncio
async def test_bridge_forward_skips_deleted_task(tmp_path):
    h = MagicMock()
    h.broadcast = AsyncMock()
    q = TaskQueue(tmp_path / "q")
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())

    b.register_task("deleted")
    await b._forward("deleted", MagicMock(type="info", task="t", raw={}))
    h.broadcast.assert_not_called()


# ── Scheduler stop cancels active tasks ──


@pytest.mark.asyncio
async def test_stop_cancels_tasks(tmp_path):
    h = MagicMock()
    h.broadcast = AsyncMock()
    q = TaskQueue(tmp_path / "q")
    b = ProgressBridge(h, q)
    b.set_loop(asyncio.get_running_loop())
    s = Scheduler(q, h, b, max_slots=6)

    mock_task = MagicMock()
    s._active_tasks.add(mock_task)
    s.stop()
    mock_task.cancel.assert_called_once()
    assert not s._running


# ── Queue lock exists ──


def test_queue_has_lock(queue):
    assert hasattr(queue, "_lock")
    assert isinstance(queue._lock, asyncio.Lock)
