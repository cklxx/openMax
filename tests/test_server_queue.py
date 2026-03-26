"""Tests for the task queue."""

from __future__ import annotations

from openmax.server.queue import QueuedTask, QueueStatus, TaskQueue, TaskSize


def test_add_and_list(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t = q.add("fix login bug", "/tmp/proj")
    assert t.status == QueueStatus.QUEUED
    assert t.task == "fix login bug"
    assert len(q.list_all()) == 1


def test_persistence_across_instances(tmp_path):
    base = tmp_path / "queue"
    q1 = TaskQueue(base)
    q1.add("task one", "/tmp")
    q1.add("task two", "/tmp")

    q2 = TaskQueue(base)
    assert len(q2.list_all()) == 2


def test_update_and_get(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t = q.add("refactor auth", "/tmp")
    t.size = TaskSize.LARGE
    t.priority = 10
    q.update(t)

    fetched = q.get(t.id)
    assert fetched.size == TaskSize.LARGE
    assert fetched.priority == 10


def test_remove(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t = q.add("delete me", "/tmp")
    assert q.remove(t.id)
    assert q.get(t.id) is None
    assert len(q.list_all()) == 0


def test_remove_nonexistent(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    assert not q.remove("nope")


def test_next_runnable_skips_unknown_size(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t = q.add("unsized", "/tmp")
    assert t.size == TaskSize.UNKNOWN
    assert q.next_runnable() is None

    t.size = TaskSize.SMALL
    q.update(t)
    assert q.next_runnable().id == t.id


def test_next_runnable_respects_priority(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t1 = q.add("low priority", "/tmp")
    t1.size = TaskSize.SMALL
    t1.priority = 90
    q.update(t1)

    t2 = q.add("high priority", "/tmp")
    t2.size = TaskSize.SMALL
    t2.priority = 10
    q.update(t2)

    assert q.next_runnable().id == t2.id


def test_running_slot_cost(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    t1 = q.add("small", "/tmp")
    t1.size = TaskSize.SMALL
    t1.status = QueueStatus.RUNNING
    q.update(t1)

    t2 = q.add("large", "/tmp")
    t2.size = TaskSize.LARGE
    t2.status = QueueStatus.RUNNING
    q.update(t2)

    assert q.running_slot_cost() == 4  # 1 + 3


def test_stats(tmp_path):
    q = TaskQueue(tmp_path / "queue")
    q.add("a", "/tmp")
    t = q.add("b", "/tmp")
    t.status = QueueStatus.DONE
    q.update(t)

    s = q.stats()
    assert s["queued"] == 1
    assert s["done"] == 1


def test_queued_task_roundtrip():
    t = QueuedTask(id="abc", task="test", cwd="/tmp", created_at="2024-01-01")
    d = t.to_dict()
    t2 = QueuedTask.from_dict(d)
    assert t2.id == "abc"
    assert t2.status == QueueStatus.QUEUED
    assert t2.size == TaskSize.UNKNOWN
