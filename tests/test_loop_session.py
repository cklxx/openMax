"""Tests for loop session tape: LoopSessionStore and build_loop_context."""

from __future__ import annotations

from openmax.loop_session import (
    LoopIteration,
    LoopSessionStore,
    build_loop_context,
)


def _store(tmp_path) -> LoopSessionStore:
    store = LoopSessionStore.__new__(LoopSessionStore)

    def _patched():
        d = tmp_path / "loops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    import openmax.loop_session as mod

    mod._loops_dir = _patched  # type: ignore[attr-defined]
    store.__init__()
    return store


def _iteration(
    n: int, done: list[str] | None = None, failed: list[str] | None = None
) -> LoopIteration:
    return LoopIteration(
        iteration=n,
        session_id=f"sess-{n}",
        started_at=f"2026-03-18T10:0{n}:00+00:00",
        completed_at=f"2026-03-18T10:0{n}:30+00:00",
        outcome_summary=f"iteration {n} work",
        completion_pct=100,
        tasks_done=done or [f"task-{n}a", f"task-{n}b"],
        tasks_failed=failed or [],
    )


# ── LoopSessionStore ──────────────────────────────────────────────────────────


def test_create_returns_session_with_unique_loop_id(tmp_path):
    store = _store(tmp_path)
    s1 = store.create("goal A", "/repo")
    s2 = store.create("goal B", "/repo")
    assert s1.loop_id != s2.loop_id
    assert s1.goal == "goal A"


def test_create_writes_header_to_disk(tmp_path):
    store = _store(tmp_path)
    session = store.create("improve openmax", "/repo")
    tape = (tmp_path / "loops" / f"{session.loop_id}.jsonl").read_text()
    assert "improve openmax" in tape
    assert '"type": "header"' in tape


def test_append_iteration_writes_to_tape(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/repo")
    store.append_iteration(session.loop_id, _iteration(1))
    tape = (tmp_path / "loops" / f"{session.loop_id}.jsonl").read_text()
    assert '"type": "iteration"' in tape
    assert "task-1a" in tape


def test_load_restores_header_and_iterations(tmp_path):
    store = _store(tmp_path)
    session = store.create("my goal", "/cwd")
    store.append_iteration(session.loop_id, _iteration(1))
    store.append_iteration(session.loop_id, _iteration(2))

    loaded = store.load(session.loop_id)

    assert loaded is not None
    assert loaded.goal == "my goal"
    assert loaded.cwd == "/cwd"
    assert len(loaded.iterations) == 2
    assert loaded.iterations[0].iteration == 1
    assert loaded.iterations[1].iteration == 2


def test_load_returns_none_for_missing_id(tmp_path):
    store = _store(tmp_path)
    assert store.load("nonexistent-id") is None


def test_tape_survives_store_recreation(tmp_path):
    """Tape persists across LoopSessionStore instances (simulates process restart)."""
    store = _store(tmp_path)
    session = store.create("persistent goal", "/cwd")
    store.append_iteration(session.loop_id, _iteration(1))

    # Create a fresh store pointing at the same dir
    store2 = _store(tmp_path)
    loaded = store2.load(session.loop_id)

    assert loaded is not None
    assert len(loaded.iterations) == 1
    assert loaded.iterations[0].outcome_summary == "iteration 1 work"


# ── build_loop_context ────────────────────────────────────────────────────────


def test_build_loop_context_empty_when_no_iterations(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/cwd")
    assert build_loop_context(session, current_iteration=1) == ""


def test_build_loop_context_includes_iteration_summary(tmp_path):
    store = _store(tmp_path)
    session = store.create("improve openmax", "/cwd")
    session.iterations.append(_iteration(1, done=["split-tools", "add-loop"]))

    ctx = build_loop_context(session, current_iteration=2)

    assert "Iteration 2" in ctx
    assert session.loop_id in ctx
    assert "iteration 1 work" in ctx
    assert "split-tools" in ctx
    assert "100%" in ctx


def test_build_loop_context_includes_do_not_repeat_warning(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/cwd")
    session.iterations.append(_iteration(1))

    ctx = build_loop_context(session, current_iteration=2)

    assert "DO NOT repeat" in ctx


def test_build_loop_context_lists_failed_tasks(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/cwd")
    session.iterations.append(_iteration(1, done=["ok-task"], failed=["broken-task"]))

    ctx = build_loop_context(session, current_iteration=2)

    assert "broken-task" in ctx
    assert "Failed" in ctx


def test_build_loop_context_covers_all_prior_iterations(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/cwd")
    for i in range(1, 4):
        session.iterations.append(_iteration(i))

    ctx = build_loop_context(session, current_iteration=4)

    assert "iteration 1 work" in ctx
    assert "iteration 2 work" in ctx
    assert "iteration 3 work" in ctx


def test_build_loop_context_caps_at_ten_iterations(tmp_path):
    store = _store(tmp_path)
    session = store.create("goal", "/cwd")
    for i in range(1, 16):  # 15 iterations — exceeds cap of 10
        session.iterations.append(_iteration(i))

    ctx = build_loop_context(session, current_iteration=16)

    # Last 10 should be present
    assert "iteration 15 work" in ctx
    assert "iteration 6 work" in ctx
    # First 5 should be omitted
    assert "iteration 1 work" not in ctx
    assert "iteration 5 work" not in ctx
    # Truncation notice present
    assert "showing last 10 of 15 iterations" in ctx
