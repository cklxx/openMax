from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import openmax.session_runtime as session_runtime
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    get_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.session_runtime import (
    ContextBuilder,
    LeadEvent,
    SessionStore,
    _compute_acceleration_ratio,
    _compute_overhead_breakdown,
    anchor_payload,
    task_hash,
)


def test_session_store_reconstructs_plan_from_events(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-a", "Build API", str(tmp_path))

    store.append_event(meta, "session.started", {"task": meta.task})
    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(phase="plan", summary="Defined two workstreams", tasks=[]),
    )
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(meta, "tool.mark_task_done", {"task_name": "API routes"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )

    snapshot = store.load_snapshot("session-a")

    assert snapshot.meta.session_id == "session-a"
    assert snapshot.plan.latest_phase == "plan"
    assert snapshot.plan.completion_pct == 100
    assert snapshot.plan.report_notes == "All subtasks closed"
    assert len(snapshot.plan.subtasks) == 1
    assert snapshot.plan.subtasks[0].name == "API routes"
    assert snapshot.plan.subtasks[0].status == "done"


def test_anchor_tasks_restore_without_full_event_history(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-b", "Resume me", str(tmp_path))

    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(
            phase="monitor",
            summary="One task still running",
            tasks=[
                {
                    "name": "UI polish",
                    "agent_type": "claude-code",
                    "prompt": "Finish polish",
                    "status": "running",
                    "pane_id": 22,
                    "pane_history": [22],
                }
            ],
        ),
    )

    snapshot = store.load_snapshot("session-b")

    assert snapshot.plan.latest_phase == "monitor"
    assert len(snapshot.plan.subtasks) == 1
    assert snapshot.plan.subtasks[0].name == "UI polish"
    assert snapshot.plan.subtasks[0].status == "running"
    assert snapshot.plan.subtasks[0].pane_id == 22


def test_context_builder_compacts_large_history_and_keeps_open_tasks(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-c", "Large task", str(tmp_path))

    tasks = []
    for index in range(18):
        tasks.append(
            {
                "name": f"done-{index}",
                "agent_type": "codex",
                "prompt": f"Done task {index}",
                "status": "done",
                "pane_id": index,
                "pane_history": [index],
            }
        )
    tasks.append(
        {
            "name": "still-open",
            "agent_type": "claude-code",
            "prompt": "Keep going",
            "status": "running",
            "pane_id": 99,
            "pane_history": [99],
        }
    )

    store.append_event(
        meta,
        "phase.anchor",
        anchor_payload(
            phase="dispatch",
            summary="Many subtasks dispatched with detailed summaries " * 8,
            tasks=tasks,
        ),
    )
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 60, "notes": "Long note " * 80},
    )

    snapshot = store.load_snapshot("session-c")
    result = ContextBuilder().build_prompt_context(snapshot, max_chars=320)

    assert len(result.text) <= 320
    assert result.compaction_summary is not None
    assert "still-open" in result.text
    assert "Completed subtasks count" in result.text


def test_session_store_reconstructs_startup_failure_activity(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-d", "Bootstrap me", str(tmp_path))

    store.append_event(
        meta,
        "session.startup_failed",
        {
            "category": "authentication",
            "stage": "sdk_client_startup",
            "detail": "Authentication required",
            "remediation": "Run `claude auth login` and retry.",
        },
    )

    snapshot = store.load_snapshot("session-d")

    assert snapshot.plan.recent_activity[-1] == (
        "Lead agent startup failed [authentication] during sdk_client_startup: "
        "Authentication required"
    )


def test_session_store_load_snapshot_skips_malformed_event_lines(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-corrupt", "Build API", str(tmp_path))

    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 50, "notes": "Halfway there"},
    )
    with store._events_path(meta.session_id).open("a", encoding="utf-8") as file_obj:
        file_obj.write("{bad json\n")

    snapshot = store.load_snapshot("session-corrupt")

    assert snapshot.plan.completion_pct == 50
    assert snapshot.load_warnings == [
        "Skipped 1 malformed event line while loading session history."
    ]


def test_lead_agent_runtime_binding_is_scoped():
    runtime = LeadAgentRuntime(cwd="/tmp/workspace", plan=None, pane_mgr=None)
    token = bind_lead_agent_runtime(runtime)

    try:
        assert get_lead_agent_runtime() is runtime
    finally:
        reset_lead_agent_runtime(token)

    with pytest.raises(RuntimeError, match="not initialized"):
        get_lead_agent_runtime()


def test_session_store_lists_recent_sessions_in_updated_order(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    older = store.create_session("session-old", "Old task", str(tmp_path))
    newer = store.create_session("session-new", "New task", str(tmp_path))

    older.updated_at = datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc).isoformat()
    newer.updated_at = (
        datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc) + timedelta(hours=1)
    ).isoformat()
    store._meta_path(older.session_id).write_text(
        json.dumps(older.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    store._meta_path(newer.session_id).write_text(
        json.dumps(newer.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sessions = store.list_sessions()

    assert [session.session_id for session in sessions] == ["session-new", "session-old"]


def test_session_store_filters_sessions_by_status_before_limit(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    completed = store.create_session("session-completed", "Completed task", str(tmp_path))
    active_old = store.create_session("session-active-old", "Active old", str(tmp_path))
    active_new = store.create_session("session-active-new", "Active new", str(tmp_path))
    failed = store.create_session("session-failed", "Failed task", str(tmp_path))

    metas = [
        (completed, "completed", datetime(2026, 3, 13, 7, 0, tzinfo=timezone.utc)),
        (active_old, "active", datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc)),
        (active_new, "active", datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc)),
        (failed, "failed", datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)),
    ]
    for meta, status, updated_at in metas:
        meta.status = status
        meta.updated_at = updated_at.isoformat()
        store._meta_path(meta.session_id).write_text(
            json.dumps(meta.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    sessions = store.list_sessions(status="active", limit=1)

    assert [session.session_id for session in sessions] == ["session-active-new"]


def test_session_store_reconstructs_terminal_outcome_and_timeline(tmp_path):
    store = SessionStore(base_dir=tmp_path)

    completed = store.create_session("session-completed", "Build API", str(tmp_path))
    completed.status = "completed"
    store._write_meta(completed)
    store.append_event(
        completed,
        "phase.anchor",
        anchor_payload(
            phase="plan",
            summary="Defined two workstreams",
            tasks=[],
            completion_pct=40,
        ),
    )
    store.append_event(
        completed,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(
        completed,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )
    store.append_event(
        completed,
        "session.completed",
        {"total_subtasks": 1, "done_subtasks": 1},
    )

    aborted = store.create_session("session-aborted", "Build UI", str(tmp_path))
    aborted.status = "aborted"
    store._write_meta(aborted)
    store.append_event(
        aborted,
        "phase.anchor",
        anchor_payload(
            phase="monitor",
            summary="Waiting on UI validation",
            tasks=[],
            completion_pct=60,
        ),
    )
    store.append_event(
        aborted,
        "session.aborted",
        {"reason": "Operator cancelled after validation stalled"},
    )

    failed = store.create_session("session-failed", "Bootstrap me", str(tmp_path))
    failed.status = "failed"
    store._write_meta(failed)
    store.append_event(
        failed,
        "session.startup_failed",
        {
            "category": "authentication",
            "stage": "sdk_client_startup",
            "detail": "Authentication required",
            "remediation": "Run `claude auth login` and retry.",
        },
    )

    completed_snapshot = store.load_snapshot("session-completed")
    aborted_snapshot = store.load_snapshot("session-aborted")
    failed_snapshot = store.load_snapshot("session-failed")

    assert completed_snapshot.plan.completion_pct == 100
    assert completed_snapshot.plan.outcome_summary == "Session completed"
    assert "Phase plan: Defined two workstreams" in completed_snapshot.plan.recent_activity
    assert completed_snapshot.plan.recent_activity[-1] == "Session completed"

    assert aborted_snapshot.plan.outcome_summary == (
        "Session aborted: Operator cancelled after validation stalled"
    )
    assert "Phase monitor: Waiting on UI validation" in aborted_snapshot.plan.recent_activity

    assert failed_snapshot.plan.outcome_summary == (
        "Lead agent startup failed [authentication] during sdk_client_startup: "
        "Authentication required"
    )


def test_session_store_derives_run_scorecard_from_existing_session_data(monkeypatch, tmp_path):
    timestamps = iter(
        [
            "2026-03-13T12:01:00+00:00",
            "2026-03-13T12:02:00+00:00",
            "2026-03-13T12:03:00+00:00",
            "2026-03-13T12:04:00+00:00",
        ]
    )
    monkeypatch.setattr(session_runtime, "utc_now_iso", lambda: next(timestamps))

    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-scorecard", "Build API", str(tmp_path))
    meta.status = "completed"
    meta.created_at = "2026-03-13T12:00:00+00:00"
    meta.updated_at = "2026-03-13T12:00:00+00:00"
    store._write_meta(meta)

    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "API routes",
            "agent_type": "codex",
            "prompt": "Implement API routes",
            "pane_id": 11,
        },
    )
    store.append_event(meta, "tool.send_text_to_pane", {"pane_id": 11, "text": "Re-run the tests"})
    store.append_event(meta, "tool.mark_task_done", {"task_name": "API routes"})
    store.append_event(
        meta,
        "tool.report_completion",
        {"completion_pct": 100, "notes": "All subtasks closed"},
    )

    snapshot = store.load_snapshot("session-scorecard")

    assert snapshot.plan.scorecard.status == "completed"
    assert snapshot.plan.scorecard.success is True
    assert snapshot.plan.scorecard.failure is False
    assert snapshot.plan.scorecard.duration_seconds == 240
    assert snapshot.plan.scorecard.subtask_count == 1
    assert snapshot.plan.scorecard.done_subtask_count == 1
    assert snapshot.plan.scorecard.manual_intervention_count == 1
    assert snapshot.plan.scorecard.completion_pct == 100
    assert snapshot.plan.scorecard.startup_failure_category is None
    assert snapshot.plan.scorecard.surface_summary.startswith(
        "status=completed | completion=100% | duration=240s"
    )
    assert (
        snapshot.plan.scorecard.surface_details
        == "subtasks=1/1 done | interventions=1 | startup_failure=n/a"
    )


def test_session_store_derives_failed_scorecard_for_startup_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(session_runtime, "utc_now_iso", lambda: "2026-03-13T12:00:05+00:00")

    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-startup-failed", "Bootstrap me", str(tmp_path))
    meta.status = "failed"
    meta.created_at = "2026-03-13T12:00:00+00:00"
    meta.updated_at = "2026-03-13T12:00:05+00:00"
    store._write_meta(meta)
    store.append_event(
        meta,
        "session.startup_failed",
        {
            "category": "authentication",
            "stage": "sdk_client_startup",
            "detail": "Authentication required",
            "remediation": "Run `claude auth login` and retry.",
        },
    )

    snapshot = store.load_snapshot("session-startup-failed")

    assert snapshot.plan.scorecard.status == "failed"
    assert snapshot.plan.scorecard.success is False
    assert snapshot.plan.scorecard.failure is True
    assert snapshot.plan.scorecard.subtask_count == 0
    assert snapshot.plan.scorecard.done_subtask_count == 0
    assert snapshot.plan.scorecard.manual_intervention_count == 0
    assert snapshot.plan.scorecard.startup_failure_category == "authentication"
    assert snapshot.plan.scorecard.surface_summary == "status=failed | completion=n/a | duration=5s"
    assert (
        snapshot.plan.scorecard.surface_details
        == "subtasks=0/0 done | interventions=0 | startup_failure=authentication"
    )


def test_completed_session_without_report_completion_infers_from_subtasks(tmp_path):
    """A session that completes without calling report_completion should NOT
    silently claim 100%.  Instead, completion_pct should be inferred from the
    ratio of done subtasks — preventing false-complete reporting."""
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-no-report", "Build API", str(tmp_path))
    meta.status = "completed"
    store._write_meta(meta)

    # Dispatch two subtasks, only mark one done.
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "backend",
            "agent_type": "codex",
            "prompt": "Build backend",
            "pane_id": 1,
        },
    )
    store.append_event(
        meta,
        "tool.dispatch_agent",
        {
            "task_name": "frontend",
            "agent_type": "claude-code",
            "prompt": "Build frontend",
            "pane_id": 2,
        },
    )
    store.append_event(meta, "tool.mark_task_done", {"task_name": "backend"})
    # No report_completion call — session just ended.
    store.append_event(
        meta,
        "session.completed",
        {"total_subtasks": 2, "done_subtasks": 1},
    )

    snapshot = store.load_snapshot("session-no-report")

    # Should be 50% (1/2 done), NOT 100%.
    assert snapshot.plan.completion_pct == 50
    assert snapshot.plan.scorecard.completion_pct == 50


def test_completed_session_no_subtasks_no_report_shows_none(tmp_path):
    """A completed session with no subtasks and no report_completion should
    show completion as None (n/a), not 100%."""
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("session-empty", "Quick task", str(tmp_path))
    meta.status = "completed"
    store._write_meta(meta)

    store.append_event(meta, "session.completed", {})

    snapshot = store.load_snapshot("session-empty")

    assert snapshot.plan.completion_pct is None
    assert snapshot.plan.scorecard.completion_pct is None


def test_find_active_session_returns_none_when_no_match(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    result = store.find_active_session("nonexistent_hash")
    assert result is None


def test_find_active_session_finds_matching_session(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    store.create_session("test-session-123", "Build a blog", "/tmp/myproject")
    th = task_hash("Build a blog", str(Path("/tmp/myproject").resolve()))
    result = store.find_active_session(th)
    assert result is not None
    assert result.session_id == "test-session-123"


def test_usage_tokens_counted_in_scorecard(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("token-test", "Goal", str(tmp_path))
    store.append_event(meta, "usage.tokens", {"input_tokens": 100, "output_tokens": 50})
    store.append_event(meta, "usage.tokens", {"input_tokens": 200, "output_tokens": 75})

    snapshot = store.load_snapshot("token-test")
    assert snapshot.plan.scorecard.total_input_tokens == 300
    assert snapshot.plan.scorecard.total_output_tokens == 125


def test_find_active_session_ignores_completed(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("done-session", "Some task", "/tmp/x")
    meta.status = "completed"
    store.save_meta(meta)
    th = task_hash("Some task", str(Path("/tmp/x").resolve()))
    result = store.find_active_session(th)
    assert result is None


# ── lead.message pruning ──────────────────────────────────────────────────────


def test_lead_message_events_are_pruned_when_over_100(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("prune-test", "Goal", str(tmp_path))

    for i in range(101):
        store.append_event(meta, "lead.message", {"text": f"msg {i}"})

    events = store.load_events(meta.session_id)
    lead_msgs = [e for e in events if e.event_type == "lead.message"]
    assert len(lead_msgs) == 50


def test_phase_anchor_events_are_never_pruned(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("anchor-prune-test", "Goal", str(tmp_path))

    for i in range(110):
        store.append_event(meta, "lead.message", {"text": f"msg {i}"})
    for i in range(5):
        store.append_event(
            meta, "phase.anchor", anchor_payload(phase="implement", summary=f"anchor {i}", tasks=[])
        )

    events = store.load_events(meta.session_id)
    anchors = [e for e in events if e.event_type == "phase.anchor"]
    assert len(anchors) == 5  # none dropped


def test_non_message_events_are_never_pruned(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("non-msg-prune-test", "Goal", str(tmp_path))

    for i in range(110):
        store.append_event(meta, "lead.message", {"text": f"msg {i}"})

    store.append_event(
        meta,
        "tool.dispatch_agent",
        {"task_name": "task-x", "agent_type": "claude-code", "prompt": "do it", "pane_id": 1},
    )
    store.append_event(meta, "tool.mark_task_done", {"task_name": "task-x"})

    events = store.load_events(meta.session_id)
    dispatch = [e for e in events if e.event_type == "tool.dispatch_agent"]
    done = [e for e in events if e.event_type == "tool.mark_task_done"]
    assert len(dispatch) == 1
    assert len(done) == 1


def test_pruning_does_not_trigger_below_100(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("no-prune-test", "Goal", str(tmp_path))

    for i in range(99):
        store.append_event(meta, "lead.message", {"text": f"msg {i}"})

    events = store.load_events(meta.session_id)
    lead_msgs = [e for e in events if e.event_type == "lead.message"]
    assert len(lead_msgs) == 99  # untouched


# ── Acceleration ratio ─────────────────────────────────────────────────────


def _make_event(event_type: str, ts: str, payload: dict | None = None) -> LeadEvent:
    return LeadEvent(
        event_id="x",
        event_type=event_type,
        session_id="s",
        cwd="/tmp",
        task_hash="h",
        timestamp=ts,
        payload=payload or {},
    )


def test_acceleration_ratio_parallel_tasks():
    """3 tasks: A(60s), B(30s dep on A), C(40s independent).
    Critical path = A + B = 90s. Wall clock 100s. Ratio = 100/90."""
    events = [
        _make_event(
            "tool.submit_plan",
            "2026-03-13T12:00:00+00:00",
            {
                "subtasks": [
                    {"name": "A", "dependencies": []},
                    {"name": "B", "dependencies": ["A"]},
                    {"name": "C", "dependencies": []},
                ]
            },
        ),
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "A", "agent_type": "codex", "pane_id": 1},
        ),
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "C", "agent_type": "codex", "pane_id": 2},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:01:00+00:00",
            {"task_name": "A"},
        ),
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:01:00+00:00",
            {"task_name": "B", "agent_type": "codex", "pane_id": 1},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:00:40+00:00",
            {"task_name": "C"},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:01:30+00:00",
            {"task_name": "B"},
        ),
    ]
    cp, ratio = _compute_acceleration_ratio(events)
    assert cp == 90.0  # A(60) + B(30)
    assert ratio == 1.0  # wall=90 / cp=90


def test_acceleration_ratio_no_deps():
    """All independent tasks: critical path = longest task."""
    events = [
        _make_event(
            "tool.submit_plan",
            "2026-03-13T12:00:00+00:00",
            {
                "subtasks": [
                    {"name": "X", "dependencies": []},
                    {"name": "Y", "dependencies": []},
                ]
            },
        ),
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "X", "agent_type": "codex", "pane_id": 1},
        ),
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "Y", "agent_type": "codex", "pane_id": 2},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:01:00+00:00",
            {"task_name": "X"},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:00:30+00:00",
            {"task_name": "Y"},
        ),
    ]
    cp, ratio = _compute_acceleration_ratio(events)
    # Critical path = max(60, 30) = 60. Wall = 60s. Ratio = 1.0.
    assert cp == 60.0
    assert ratio == 1.0


def test_acceleration_ratio_single_task():
    """Single task: ratio = 1.0."""
    events = [
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "only", "agent_type": "codex", "pane_id": 1},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:01:00+00:00",
            {"task_name": "only"},
        ),
    ]
    cp, ratio = _compute_acceleration_ratio(events)
    assert cp == 60.0
    assert ratio == 1.0


# ── Overhead breakdown ─────────────────────────────────────────────────────


def test_overhead_breakdown_basic():
    """Dispatch/monitor/merge events classify into correct buckets."""
    events = [
        _make_event(
            "tool.dispatch_agent",
            "2026-03-13T12:00:00+00:00",
            {"task_name": "T1", "agent_type": "codex", "pane_id": 1},
        ),
        _make_event(
            "tool.read_pane_output",
            "2026-03-13T12:00:10+00:00",
            {"pane_id": 1},
        ),
        _make_event(
            "tool.mark_task_done",
            "2026-03-13T12:01:00+00:00",
            {"task_name": "T1"},
        ),
        _make_event(
            "tool.merge_agent_branch",
            "2026-03-13T12:01:05+00:00",
            {"task_name": "T1", "status": "merged", "commit": "abc123"},
        ),
    ]
    result = _compute_overhead_breakdown(events, 65.0)
    assert result is not None
    assert result.agent_work_seconds == 60.0
    assert result.monitor_seconds == 10.0
    assert result.merge_seconds == 5.0
    # other = max(total - agent - non_agent, 0) — agent work overlaps gaps
    assert result.other_seconds == 0.0


def test_overhead_breakdown_no_events():
    """Empty events returns None."""
    result = _compute_overhead_breakdown([], 100.0)
    assert result is None
