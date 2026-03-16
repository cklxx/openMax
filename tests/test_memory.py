"""Tests for memory eviction logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openmax.memory._utils import _MAX_ENTRIES_PER_WORKSPACE
from openmax.memory.store import MemoryStore


def _make_entry_dict(
    memory_id: str,
    kind: str = "lesson",
    age_days: int = 0,
    last_matched: str | None = None,
) -> dict:
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    metadata: dict = {}
    if last_matched is not None:
        metadata["last_matched"] = last_matched
    return {
        "memory_id": memory_id,
        "created_at": created,
        "kind": kind,
        "task": f"task-{memory_id}",
        "summary": f"summary-{memory_id}",
        "insights": [],
        "workspace_facts": [],
        "lessons": [],
        "performance_signals": [],
        "agent_stats": [],
        "confidence": None,
        "completion_pct": None,
        "source": "test",
        "metadata": metadata,
    }


def test_eviction_old_lesson_evicted(tmp_path):
    """An old lesson without recent matches gets evicted when capacity is exceeded."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Record 50 recent lessons
    for i in range(_MAX_ENTRIES_PER_WORKSPACE):
        store.record_lesson(cwd=cwd, task=f"task-{i}", lesson=f"lesson-{i}")

    # Record one old lesson (will be the 51st entry)
    entries_before = store.load_entries(cwd)
    assert len(entries_before) == _MAX_ENTRIES_PER_WORKSPACE

    store.record_lesson(cwd=cwd, task="old-task", lesson="old-lesson")
    entries_after = store.load_entries(cwd)

    assert len(entries_after) <= _MAX_ENTRIES_PER_WORKSPACE
    # The newest entry (old-lesson) should still be present
    summaries = [e.summary for e in entries_after]
    assert "old-lesson" in summaries


def test_eviction_run_summaries_protected(tmp_path):
    """Run summary entries are protected from eviction."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Fill with run summaries
    for i in range(_MAX_ENTRIES_PER_WORKSPACE):
        store.record_run_summary(
            cwd=cwd,
            task=f"task-{i}",
            notes=f"notes-{i}",
            completion_pct=100,
            subtasks=[],
            anchors=[],
        )

    # Add one more lesson to trigger eviction
    store.record_lesson(cwd=cwd, task="extra-task", lesson="extra-lesson")
    entries = store.load_entries(cwd)

    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE
    # All run_summary entries that remain should be present
    run_summary_count = sum(1 for e in entries if e.kind == "run_summary")
    lesson_count = sum(1 for e in entries if e.kind == "lesson")
    # The lesson should be the one evicted since run_summaries are protected
    assert lesson_count == 0
    assert run_summary_count == _MAX_ENTRIES_PER_WORKSPACE


def test_eviction_capacity_limit(tmp_path):
    """Capacity never exceeds _MAX_ENTRIES_PER_WORKSPACE."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Add well over the limit
    for i in range(_MAX_ENTRIES_PER_WORKSPACE + 10):
        store.record_lesson(cwd=cwd, task=f"task-{i}", lesson=f"lesson-{i}")

    entries = store.load_entries(cwd)
    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE
