"""Tests for tui/bridge.py — DashboardBridge thread safety and TuiDashboard."""

from __future__ import annotations

import threading

from openmax.tui.bridge import DashboardBridge


def test_initial_state():
    bridge = DashboardBridge("test goal")
    snap = bridge.get_snapshot()
    assert snap.goal == "test goal"
    assert snap.phase == ""
    assert snap.subtasks == {}
    assert snap.tool_events == []


def test_update_phase():
    bridge = DashboardBridge("g")
    bridge.update_phase("implement", pct=50)
    snap = bridge.get_snapshot()
    assert snap.phase == "implement"
    assert snap.phase_pct == 50


def test_update_subtask_creates_entry():
    bridge = DashboardBridge("g")
    bridge.update_subtask("auth", "codex", 1, "running", started_at=1.0)
    snap = bridge.get_snapshot()
    assert "auth" in snap.subtasks
    info = snap.subtasks["auth"]
    assert info.agent == "codex"
    assert info.status == "running"
    assert info.started_at is not None


def test_update_subtask_preserves_start_time():
    bridge = DashboardBridge("g")
    bridge.update_subtask("t", "codex", 1, "running", started_at=1.0)
    first_start = bridge.get_snapshot().subtasks["t"].started_at
    bridge.update_subtask("t", "codex", 1, "done", finished_at=2.0)
    snap = bridge.get_snapshot()
    assert snap.subtasks["t"].started_at == first_start
    assert snap.subtasks["t"].finished_at is not None


def test_update_pane_activity():
    bridge = DashboardBridge("g")
    bridge.update_pane_activity(42, "compiling...")
    snap = bridge.get_snapshot()
    assert snap.pane_activity[42] == "compiling..."


def test_add_tool_event():
    bridge = DashboardBridge("g")
    bridge.add_tool_event("dispatched auth", category="dispatch")
    snap = bridge.get_snapshot()
    assert len(snap.tool_events) == 1
    assert snap.tool_events[0]["text"] == "dispatched auth"
    assert snap.tool_events[0]["category"] == "dispatch"


def test_tool_events_bounded():
    bridge = DashboardBridge("g")
    for i in range(1100):
        bridge.add_tool_event(f"event {i}")
    snap = bridge.get_snapshot()
    assert len(snap.tool_events) == 1000
    assert snap.tool_events[0]["text"] == "event 100"


def test_set_session_metrics():
    bridge = DashboardBridge("g")
    bridge.set_session_metrics(
        total_input_tokens=5000,
        total_output_tokens=3000,
        acceleration_ratio=2.5,
    )
    snap = bridge.get_snapshot()
    assert snap.total_input_tokens == 5000
    assert snap.total_output_tokens == 3000
    assert snap.acceleration_ratio == 2.5


def test_set_session_metrics_computes_cost():
    bridge = DashboardBridge("g")
    bridge.set_session_metrics(total_input_tokens=10000, total_output_tokens=5000)
    snap = bridge.get_snapshot()
    assert snap.total_cost_usd > 0


def test_set_session_metrics_with_task_tokens():
    bridge = DashboardBridge("g")
    bridge.set_session_metrics(
        total_input_tokens=10000,
        total_output_tokens=5000,
        task_tokens={"auth": 8000, "tests": 7000},
    )
    snap = bridge.get_snapshot()
    assert snap.task_tokens == {"auth": 8000, "tests": 7000}


def test_task_tokens_default_empty():
    bridge = DashboardBridge("g")
    snap = bridge.get_snapshot()
    assert snap.task_tokens == {}
    assert snap.total_cost_usd == 0.0


def test_set_dispatch_prompt():
    bridge = DashboardBridge("g")
    bridge.set_dispatch_prompt("auth", "implement auth service")
    snap = bridge.get_snapshot()
    assert snap.dispatch_prompts["auth"] == "implement auth service"


def test_bump_monitor_count():
    bridge = DashboardBridge("g")
    bridge.bump_monitor_count()
    bridge.bump_monitor_count()
    snap = bridge.get_snapshot()
    assert snap.monitor_count == 2


def test_set_task_dependencies():
    bridge = DashboardBridge("g")
    deps = {"auth": ["research"], "api": ["research"]}
    bridge.set_task_dependencies(deps)
    snap = bridge.get_snapshot()
    assert snap.task_dependencies == deps


def test_task_dependencies_default_empty():
    bridge = DashboardBridge("g")
    snap = bridge.get_snapshot()
    assert snap.task_dependencies == {}


def test_snapshot_is_deep_copy():
    bridge = DashboardBridge("g")
    bridge.update_subtask("t", "codex", 1, "running", started_at=1.0)
    snap = bridge.get_snapshot()
    snap.subtasks["t"].status = "mutated"
    snap.tool_events.append({"text": "injected"})
    fresh = bridge.get_snapshot()
    assert fresh.subtasks["t"].status == "running"
    assert len(fresh.tool_events) == 0


def test_thread_safety():
    bridge = DashboardBridge("g")
    errors: list[str] = []

    def writer(tid: int):
        try:
            for i in range(200):
                bridge.update_subtask(f"task-{tid}", "agent", tid, "running")
                bridge.add_tool_event(f"event-{tid}-{i}")
                bridge.update_pane_activity(tid, f"line-{i}")
        except Exception as exc:
            errors.append(str(exc))

    def reader():
        try:
            for _ in range(200):
                snap = bridge.get_snapshot()
                _ = len(snap.subtasks)
                _ = len(snap.tool_events)
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []


def test_tui_dashboard_delegates_to_bridge():
    from openmax.tui.bridge import TuiDashboard

    dash = TuiDashboard.__new__(TuiDashboard)
    dash._bridge = DashboardBridge("g")
    dash._verbose = False
    dash._app = None
    dash._thread = None

    dash.update_phase("plan", pct=10)
    dash.update_subtask("x", "claude", None, "pending")
    dash.add_tool_event("hello")
    dash.set_dispatch_prompt("x", "do stuff")
    dash.bump_monitor_count()
    dash.set_session_metrics(total_input_tokens=100)
    dash.update_pane_activity(1, "output")
    dash.set_task_dependencies({"x": ["y"]})

    snap = dash._bridge.get_snapshot()
    assert snap.phase == "plan"
    assert "x" in snap.subtasks
    assert len(snap.tool_events) == 1
    assert snap.dispatch_prompts["x"] == "do stuff"
    assert snap.monitor_count == 1
    assert snap.total_input_tokens == 100
    assert snap.pane_activity[1] == "output"


# ── Task progress ────────────────────────────────────────────────


def test_update_task_progress():
    bridge = DashboardBridge("g")
    bridge.update_task_progress("auth", 42)
    snap = bridge.get_snapshot()
    assert snap.task_progress["auth"] == 42


def test_task_progress_clamps():
    bridge = DashboardBridge("g")
    bridge.update_task_progress("a", -10)
    bridge.update_task_progress("b", 200)
    snap = bridge.get_snapshot()
    assert snap.task_progress["a"] == 0
    assert snap.task_progress["b"] == 100


def test_task_progress_deep_copy():
    bridge = DashboardBridge("g")
    bridge.update_task_progress("t", 50)
    snap = bridge.get_snapshot()
    snap.task_progress["t"] = 99
    assert bridge.get_snapshot().task_progress["t"] == 50


def test_tui_dashboard_delegates_task_progress():
    from openmax.tui.bridge import TuiDashboard

    dash = TuiDashboard.__new__(TuiDashboard)
    dash._bridge = DashboardBridge("g")
    dash._verbose = False
    dash._app = None
    dash._thread = None

    dash.update_task_progress("auth", 75)
    snap = dash._bridge.get_snapshot()
    assert snap.task_progress["auth"] == 75
