"""Tests for the usage tracking module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openmax.usage import SessionUsage, UsageStore, usage_from_result


@dataclass
class FakeResultMessage:
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int = 0
    duration_api_ms: int = 0
    num_turns: int = 0


def test_usage_from_result_extracts_all_fields():
    msg = FakeResultMessage(
        total_cost_usd=0.1234,
        usage={
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 100,
        },
        duration_ms=5000,
        duration_api_ms=3000,
        num_turns=3,
    )
    u = usage_from_result("test-session", msg)
    assert u.session_id == "test-session"
    assert u.cost_usd == 0.1234
    assert u.input_tokens == 1000
    assert u.output_tokens == 500
    assert u.cache_read_tokens == 200
    assert u.cache_creation_tokens == 100
    assert u.duration_ms == 5000
    assert u.duration_api_ms == 3000
    assert u.num_turns == 3
    assert u.total_tokens == 1500


def test_usage_from_result_handles_none_usage():
    msg = FakeResultMessage(total_cost_usd=None, usage=None)
    u = usage_from_result("s1", msg)
    assert u.cost_usd == 0.0
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.total_tokens == 0


def test_session_usage_format_methods():
    u = SessionUsage(
        session_id="s1",
        cost_usd=1.5,
        input_tokens=10000,
        output_tokens=5000,
        cache_read_tokens=2000,
        duration_ms=125000,
        num_turns=7,
    )
    assert u.format_cost() == "$1.5000"
    assert u.format_duration() == "2m 5s"
    assert u.total_tokens == 15000
    assert "10,000 in" in u.format_tokens()
    assert "5,000 out" in u.format_tokens()
    assert "2,000 cache-read" in u.format_tokens()
    summary = u.summary_line()
    assert "$1.5000" in summary
    assert "15,000" in summary
    assert "7" in summary


def test_session_usage_format_duration_short():
    u = SessionUsage(session_id="s1", duration_ms=45200)
    assert u.format_duration() == "45.2s"


def test_usage_store_save_load_roundtrip(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    u = SessionUsage(
        session_id="test-roundtrip",
        cost_usd=0.05,
        input_tokens=100,
        output_tokens=50,
        duration_ms=2000,
        num_turns=2,
    )
    store.save(u)
    loaded = store.load("test-roundtrip")
    assert loaded is not None
    assert loaded.session_id == "test-roundtrip"
    assert loaded.cost_usd == 0.05
    assert loaded.input_tokens == 100
    assert loaded.output_tokens == 50
    assert loaded.total_tokens == 150
    assert loaded.num_turns == 2


def test_usage_store_load_missing(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    assert store.load("nonexistent") is None


def test_usage_store_list_all(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    for i in range(3):
        u = SessionUsage(
            session_id=f"session-{i}",
            cost_usd=float(i),
            num_turns=i,
        )
        store.save(u)

    records = store.list_all()
    assert len(records) == 3


def test_usage_store_list_all_with_limit(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    for i in range(5):
        u = SessionUsage(session_id=f"s-{i}", cost_usd=float(i))
        store.save(u)
    records = store.list_all(limit=2)
    assert len(records) == 2


def test_usage_store_aggregate(tmp_path):
    store = UsageStore(base_dir=tmp_path)
    records = [
        SessionUsage(
            session_id="a",
            cost_usd=1.0,
            input_tokens=100,
            output_tokens=50,
            num_turns=3,
            duration_ms=1000,
        ),
        SessionUsage(
            session_id="b",
            cost_usd=2.0,
            input_tokens=200,
            output_tokens=100,
            num_turns=5,
            duration_ms=2000,
        ),
    ]
    agg = store.aggregate(records)
    assert agg.cost_usd == 3.0
    assert agg.input_tokens == 300
    assert agg.output_tokens == 150
    assert agg.total_tokens == 450
    assert agg.num_turns == 8
    assert agg.duration_ms == 3000
