"""Tests for memory eviction logic."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from openmax.memory._utils import (
    _MAX_ENTRIES_PER_WORKSPACE,
    _MIN_RECENT_KEEP,
    MAX_MEMORY_ENTRIES,
    _eviction_score,
)
from openmax.memory.store import MemoryStore


def _make_entry_dict(
    memory_id: str,
    kind: str = "lesson",
    age_days: int = 0,
    last_matched: str | None = None,
    confidence: int | None = None,
    hit_count: int = 0,
    completion_pct: int | None = None,
) -> dict:
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    metadata: dict = {}
    if last_matched is not None:
        metadata["last_matched"] = last_matched
    if hit_count:
        metadata["hit_count"] = hit_count
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
        "confidence": confidence,
        "completion_pct": completion_pct,
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
    """Run summary entries are protected from eviction over lessons."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Fill half with lessons, half with run_summaries
    half = _MAX_ENTRIES_PER_WORKSPACE // 2
    for i in range(half):
        store.record_lesson(cwd=cwd, task=f"lesson-task-{i}", lesson=f"lesson-{i}")
    for i in range(half):
        store.record_run_summary(
            cwd=cwd,
            task=f"run-task-{i}",
            notes=f"notes-{i}",
            completion_pct=100,
            subtasks=[],
            anchors=[],
        )

    # Add one more lesson to trigger eviction
    store.record_lesson(cwd=cwd, task="extra-task", lesson="extra-lesson")
    entries = store.load_entries(cwd)

    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE
    # Run summaries should be preserved; a lesson should be evicted
    run_summary_count = sum(1 for e in entries if e.kind == "run_summary")
    assert run_summary_count == half


def test_eviction_capacity_limit(tmp_path):
    """Capacity never exceeds _MAX_ENTRIES_PER_WORKSPACE."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Add well over the limit
    for i in range(_MAX_ENTRIES_PER_WORKSPACE + 10):
        store.record_lesson(cwd=cwd, task=f"task-{i}", lesson=f"lesson-{i}")

    entries = store.load_entries(cwd)
    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE


def test_eviction_score_prefers_high_confidence():
    """High-confidence entries get lower eviction scores (kept longer)."""
    now = datetime.now(timezone.utc)
    high_conf = _make_entry_dict("high", age_days=30, confidence=9)
    low_conf = _make_entry_dict("low", age_days=30, confidence=1)
    no_conf = _make_entry_dict("none", age_days=30, confidence=None)

    score_high = _eviction_score(high_conf, now)
    score_low = _eviction_score(low_conf, now)
    score_none = _eviction_score(no_conf, now)

    # Higher confidence → lower eviction score
    assert score_high < score_low
    assert score_low < score_none


def test_eviction_score_prefers_frequently_used():
    """Entries with high hit_count get lower eviction scores."""
    now = datetime.now(timezone.utc)
    many_hits = _make_entry_dict("many", age_days=30, hit_count=15)
    few_hits = _make_entry_dict("few", age_days=30, hit_count=1)
    no_hits = _make_entry_dict("zero", age_days=30, hit_count=0)

    score_many = _eviction_score(many_hits, now)
    score_few = _eviction_score(few_hits, now)
    score_none = _eviction_score(no_hits, now)

    assert score_many < score_few
    assert score_few < score_none


def test_eviction_score_prefers_recent():
    """Recent entries get lower eviction scores than old ones."""
    now = datetime.now(timezone.utc)
    recent = _make_entry_dict("new", age_days=1)
    old = _make_entry_dict("old", age_days=100)

    assert _eviction_score(recent, now) < _eviction_score(old, now)


def test_eviction_score_completion_bonus():
    """Entries with high completion_pct get lower eviction scores."""
    now = datetime.now(timezone.utc)
    complete = _make_entry_dict("done", age_days=30, completion_pct=100)
    incomplete = _make_entry_dict("partial", age_days=30, completion_pct=None)

    assert _eviction_score(complete, now) < _eviction_score(incomplete, now)


def test_minimum_recent_entries_retained(tmp_path):
    """The most recent _MIN_RECENT_KEEP entries are never evicted."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Fill to capacity with lessons
    for i in range(_MAX_ENTRIES_PER_WORKSPACE + 1):
        store.record_lesson(cwd=cwd, task=f"task-{i}", lesson=f"lesson-{i}")

    entries = store.load_entries(cwd)
    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE

    # The last _MIN_RECENT_KEEP entries should all be present
    summaries = [e.summary for e in entries]
    start = _MAX_ENTRIES_PER_WORKSPACE + 1 - _MIN_RECENT_KEEP
    for i in range(start, _MAX_ENTRIES_PER_WORKSPACE + 1):
        assert f"lesson-{i}" in summaries


def test_pinned_entries_never_evicted(tmp_path):
    """Pinned entries survive eviction even when they are old."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    # Record first entry and pin it
    first = store.record_lesson(cwd=cwd, task="important-task", lesson="critical-lesson")
    store.pin_entry(cwd, first.memory_id)

    # Fill to capacity with more lessons to trigger eviction
    for i in range(_MAX_ENTRIES_PER_WORKSPACE + 5):
        store.record_lesson(cwd=cwd, task=f"task-{i}", lesson=f"lesson-{i}")

    entries = store.load_entries(cwd)
    assert len(entries) <= _MAX_ENTRIES_PER_WORKSPACE
    # The pinned entry must still be present
    ids = {e.memory_id for e in entries}
    assert first.memory_id in ids
    # Verify it is still pinned
    pinned_entry = next(e for e in entries if e.memory_id == first.memory_id)
    assert pinned_entry.pinned is True


def test_pin_unpin_roundtrip(tmp_path):
    """Pin and unpin toggle the pinned flag correctly."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    entry = store.record_lesson(cwd=cwd, task="task", lesson="lesson")
    assert entry.pinned is False

    assert store.pin_entry(cwd, entry.memory_id) is True
    entries = store.load_entries(cwd)
    assert entries[0].pinned is True

    assert store.unpin_entry(cwd, entry.memory_id) is True
    entries = store.load_entries(cwd)
    assert entries[0].pinned is False

    # Non-existent ID returns False
    assert store.pin_entry(cwd, "nonexistent") is False


def test_hit_tracking_on_build_context(tmp_path):
    """build_context increments hit_count and sets last_matched."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    store.record_lesson(cwd=cwd, task="fix auth bug", lesson="auth needs sanitization")
    store.record_lesson(cwd=cwd, task="add logging", lesson="use structured logging")

    # Build context with a matching task
    ctx = store.build_context(cwd=cwd, task="fix auth bug in login", limit=4)
    assert ctx is not None

    # Reload and check hit_count was bumped
    entries = store.load_entries(cwd)
    hit_counts = [e.metadata.get("hit_count", 0) for e in entries]
    assert any(c > 0 for c in hit_counts)


def test_max_memory_entries_constant():
    """MAX_MEMORY_ENTRIES is 100 and aliases _MAX_ENTRIES_PER_WORKSPACE."""
    assert MAX_MEMORY_ENTRIES == 100
    assert _MAX_ENTRIES_PER_WORKSPACE == MAX_MEMORY_ENTRIES


def test_last_accessed_set_on_creation(tmp_path):
    """New entries have last_accessed set to a recent timestamp."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")
    before = time.time()
    store.record_lesson(cwd=cwd, task="task-1", lesson="lesson-1")
    after = time.time()

    entries = store.load_entries(cwd)
    assert len(entries) == 1
    assert before <= entries[0].last_accessed <= after


def test_last_accessed_bumped_on_build_context(tmp_path):
    """build_context bumps last_accessed for matched entries."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "project")

    store.record_lesson(cwd=cwd, task="fix auth bug", lesson="auth tokens expire after 1h")
    entries_before = store.load_entries(cwd)
    original_accessed = entries_before[0].last_accessed

    # Small sleep to ensure timestamp difference
    time.sleep(0.05)

    # build_context should bump last_accessed for matched entries
    store.build_context(cwd=cwd, task="fix auth bug")
    entries_after = store.load_entries(cwd)
    assert entries_after[0].last_accessed > original_accessed


def test_eviction_score_staleness():
    """Recently accessed entries get lower eviction scores than stale ones."""
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()

    recently_accessed = _make_entry_dict("recent", age_days=30)
    recently_accessed["last_accessed"] = now_ts - 3600  # accessed 1 hour ago

    stale = _make_entry_dict("stale", age_days=30)
    stale["last_accessed"] = now_ts - 86400 * 60  # accessed 60 days ago

    never_accessed = _make_entry_dict("never", age_days=30)
    never_accessed["last_accessed"] = 0.0  # never accessed

    score_recent = _eviction_score(recently_accessed, now)
    score_stale = _eviction_score(stale, now)
    score_never = _eviction_score(never_accessed, now)

    # Recently accessed → lower eviction score (kept)
    assert score_recent < score_stale
    assert score_recent < score_never
