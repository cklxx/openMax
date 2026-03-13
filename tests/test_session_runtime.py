from __future__ import annotations

import pytest

from openmax.session_runtime import ContextBuilder, SessionStore, anchor_payload
from openmax.session_runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    get_lead_agent_runtime,
    reset_lead_agent_runtime,
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


def test_lead_agent_runtime_binding_is_scoped():
    runtime = LeadAgentRuntime(cwd="/tmp/workspace", plan=None, pane_mgr=None)
    token = bind_lead_agent_runtime(runtime)

    try:
        assert get_lead_agent_runtime() is runtime
    finally:
        reset_lead_agent_runtime(token)

    with pytest.raises(RuntimeError, match="not initialized"):
        get_lead_agent_runtime()
