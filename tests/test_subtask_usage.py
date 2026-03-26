"""Tests for per-subtask token/usage tracking end-to-end."""

from __future__ import annotations

from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.usage import SessionUsage, UsageStore, _load_usage_from_dict

# ── _apply_subtask_usage ──────────────────────────────────────────


def test_apply_subtask_usage_populates_fields(monkeypatch):
    from openmax.lead_agent.tools._helpers import _apply_subtask_usage

    st = SubTask(name="api-task", agent_type="claude-code", prompt="x", status=TaskStatus.RUNNING)
    plan = type("P", (), {"subtasks": [st]})()
    runtime = type("R", (), {"plan": plan})()
    monkeypatch.setattr("openmax.lead_agent.tools._helpers._runtime", lambda: runtime)

    raw = {"input_tokens": 5000, "output_tokens": 12000, "cost_usd": 0.153}
    _apply_subtask_usage("api-task", raw)

    assert st.input_tokens == 5000
    assert st.output_tokens == 12000
    assert st.tokens_used == 17000
    assert st.cost_usd == 0.153
    assert st.usage_source == "reported"


def test_apply_subtask_usage_skips_when_no_tokens(monkeypatch):
    from openmax.lead_agent.tools._helpers import _apply_subtask_usage

    st = SubTask(name="t1", agent_type="codex", prompt="x", usage_source="estimated")
    plan = type("P", (), {"subtasks": [st]})()
    runtime = type("R", (), {"plan": plan})()
    monkeypatch.setattr("openmax.lead_agent.tools._helpers._runtime", lambda: runtime)

    _apply_subtask_usage("t1", {"summary": "done"})
    assert st.usage_source == "estimated"
    assert st.input_tokens == 0


def test_apply_subtask_usage_ignores_unknown_task(monkeypatch):
    from openmax.lead_agent.tools._helpers import _apply_subtask_usage

    st = SubTask(name="t1", agent_type="codex", prompt="x")
    plan = type("P", (), {"subtasks": [st]})()
    runtime = type("R", (), {"plan": plan})()
    monkeypatch.setattr("openmax.lead_agent.tools._helpers._runtime", lambda: runtime)

    _apply_subtask_usage("unknown-task", {"input_tokens": 100, "output_tokens": 200})
    assert st.input_tokens == 0


# ── SessionUsage with subtask_usage ───────────────────────────────


def test_session_usage_subtask_total_tokens():
    usage = SessionUsage(
        session_id="s1",
        input_tokens=100,
        output_tokens=200,
        subtask_usage=[
            {"task_name": "t1", "input_tokens": 1000, "output_tokens": 2000, "cost_usd": 0.03},
            {"task_name": "t2", "input_tokens": 500, "output_tokens": 800, "cost_usd": 0.01},
        ],
    )
    assert usage.subtask_total_tokens == 4300
    assert usage.total_tokens == 300


def test_session_usage_session_total_line_with_subtasks():
    usage = SessionUsage(
        session_id="s1",
        cost_usd=0.50,
        input_tokens=100,
        output_tokens=200,
        subtask_usage=[
            {"task_name": "t1", "input_tokens": 1000, "output_tokens": 2000, "cost_usd": 0.10},
        ],
    )
    line = usage.session_total_line()
    assert "Total: $0.6000" in line
    assert "Agents: 1" in line


def test_session_usage_session_total_line_no_subtasks():
    usage = SessionUsage(session_id="s1", cost_usd=0.50, input_tokens=100, output_tokens=200)
    line = usage.session_total_line()
    assert "Cost: $0.5000" in line


# ── Backward-compatible loading ───────────────────────────────────


def test_load_usage_from_dict_backward_compat():
    data = {
        "session_id": "old-session",
        "cost_usd": 0.10,
        "input_tokens": 500,
        "output_tokens": 1000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_ms": 5000,
        "duration_api_ms": 3000,
        "num_turns": 5,
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    usage = _load_usage_from_dict(data)
    assert usage.subtask_usage == []
    assert usage.total_session_cost_usd == 0.0


def test_load_usage_from_dict_with_subtask_usage():
    data = {
        "session_id": "new-session",
        "cost_usd": 0.10,
        "input_tokens": 500,
        "output_tokens": 1000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_ms": 5000,
        "duration_api_ms": 3000,
        "num_turns": 5,
        "subtask_usage": [{"task_name": "t1", "input_tokens": 100, "output_tokens": 200}],
        "total_session_cost_usd": 0.15,
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    usage = _load_usage_from_dict(data)
    assert len(usage.subtask_usage) == 1
    assert usage.total_session_cost_usd == 0.15


# ── UsageStore roundtrip with subtask_usage ───────────────────────


def test_usage_store_roundtrip_with_subtask_usage(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    usage = SessionUsage(
        session_id="rt-test",
        cost_usd=0.25,
        input_tokens=1000,
        output_tokens=2000,
        subtask_usage=[
            {
                "task_name": "api-impl",
                "agent_type": "claude-code",
                "input_tokens": 5000,
                "output_tokens": 10000,
                "cost_usd": 0.135,
                "source": "reported",
            },
        ],
        total_session_cost_usd=0.385,
    )
    store.save(usage)
    loaded = store.load("rt-test")
    assert loaded is not None
    assert len(loaded.subtask_usage) == 1
    assert loaded.subtask_usage[0]["task_name"] == "api-impl"
    assert loaded.subtask_usage[0]["source"] == "reported"
    assert loaded.total_session_cost_usd == 0.385
