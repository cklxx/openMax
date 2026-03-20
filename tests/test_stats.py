"""Tests for openmax.stats — session statistics store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmax.stats import (
    COST_MULTIPLIER_RANGE,
    DECAY_ALPHA,
    SCHEMA_VERSION,
    SessionStats,
    clamp,
    load_stats,
    save_stats,
    update_stats,
)

# --- clamp ---


def test_clamp_within_range():
    assert clamp(5.0, 1.0, 10.0) == 5.0


def test_clamp_below_min():
    assert clamp(-1.0, 0.0, 10.0) == 0.0


def test_clamp_above_max():
    assert clamp(15.0, 0.0, 10.0) == 10.0


# --- SessionStats defaults ---


def test_session_stats_defaults():
    stats = SessionStats()
    assert stats.schema_version == SCHEMA_VERSION
    assert stats.sessions_count == 0
    assert stats.avg_tokens_per_task == 0.0
    assert stats.cost_multiplier_actual_vs_estimated == 1.0
    assert stats.merge_conflict_rate_by_dir == {}
    assert stats.avg_task_duration_by_type == {}


# --- load_stats ---


def test_load_stats_no_file_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty_home")
    stats = load_stats(str(tmp_path / "nonexistent"))
    assert stats.sessions_count == 0
    assert stats.schema_version == SCHEMA_VERSION


def test_load_stats_from_project_dir(tmp_path: Path):
    stats_path = tmp_path / ".openmax" / "stats" / "session_stats.json"
    stats_path.parent.mkdir(parents=True)
    data = SessionStats(sessions_count=5)
    stats_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, **data.__dict__}))
    loaded = load_stats(str(tmp_path))
    assert loaded.sessions_count == 5


def test_load_stats_falls_back_to_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    global_path = tmp_path / ".openmax" / "stats" / "session_stats.json"
    global_path.parent.mkdir(parents=True)
    data = SessionStats(sessions_count=3)
    global_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, **data.__dict__}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    loaded = load_stats(str(tmp_path / "no_project_stats"))
    assert loaded.sessions_count == 3


def test_load_stats_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty_home")
    stats_path = tmp_path / ".openmax" / "stats" / "session_stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text("not json at all {{{")
    loaded = load_stats(str(tmp_path))
    assert loaded.sessions_count == 0


def test_load_stats_old_schema_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty_home")
    stats_path = tmp_path / ".openmax" / "stats" / "session_stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(json.dumps({"schema_version": 999, "sessions_count": 10}))
    loaded = load_stats(str(tmp_path))
    assert loaded.sessions_count == 0


# --- save_stats ---


def test_save_stats_creates_project_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    stats = SessionStats(sessions_count=7)
    save_stats(stats, str(tmp_path / "project"))
    project_path = tmp_path / "project" / ".openmax" / "stats" / "session_stats.json"
    global_path = tmp_path / "home" / ".openmax" / "stats" / "session_stats.json"
    assert project_path.exists()
    assert global_path.exists()
    data = json.loads(project_path.read_text())
    assert data["sessions_count"] == 7


def test_save_stats_global_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stats = SessionStats(sessions_count=2)
    save_stats(stats)
    global_path = tmp_path / ".openmax" / "stats" / "session_stats.json"
    assert global_path.exists()


def test_save_stats_permission_error_no_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/readonly"))
    save_stats(SessionStats())


# --- update_stats ---


def test_update_stats_increments_session_count():
    current = SessionStats(sessions_count=5)
    updated = update_stats(current, {})
    assert updated.sessions_count == 6


def test_update_stats_ema_tokens():
    current = SessionStats(avg_tokens_per_task=100.0)
    updated = update_stats(current, {"avg_tokens_per_task": 200.0})
    expected = DECAY_ALPHA * 200.0 + (1 - DECAY_ALPHA) * 100.0
    assert updated.avg_tokens_per_task == pytest.approx(expected)


def test_update_stats_cost_multiplier_clamped():
    current = SessionStats(cost_multiplier_actual_vs_estimated=4.0)
    updated = update_stats(current, {"cost_multiplier_actual_vs_estimated": 100.0})
    assert updated.cost_multiplier_actual_vs_estimated <= COST_MULTIPLIER_RANGE[1]


def test_update_stats_dict_ema_merges_keys():
    current = SessionStats(avg_task_duration_by_type={"build": 10.0})
    updated = update_stats(current, {"avg_task_duration_by_type": {"build": 20.0, "test": 5.0}})
    assert "build" in updated.avg_task_duration_by_type
    assert "test" in updated.avg_task_duration_by_type
    expected_build = DECAY_ALPHA * 20.0 + (1 - DECAY_ALPHA) * 10.0
    assert updated.avg_task_duration_by_type["build"] == pytest.approx(expected_build)
    assert updated.avg_task_duration_by_type["test"] == 5.0


def test_update_stats_preserves_existing_dict_keys():
    current = SessionStats(merge_conflict_rate_by_dir={"src": 0.1, "lib": 0.2})
    updated = update_stats(current, {"merge_conflict_rate_by_dir": {"src": 0.3}})
    assert "lib" in updated.merge_conflict_rate_by_dir
    assert updated.merge_conflict_rate_by_dir["lib"] == 0.2


def test_update_stats_empty_new_data():
    current = SessionStats(sessions_count=3, avg_tokens_per_task=50.0)
    updated = update_stats(current, {})
    assert updated.sessions_count == 4
    assert updated.avg_tokens_per_task == pytest.approx(50.0)


# --- round-trip ---


def test_save_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    original = SessionStats(
        sessions_count=10,
        avg_tokens_per_task=500.0,
        merge_conflict_rate_by_dir={"src": 0.05},
        avg_task_duration_by_type={"build": 120.0},
        cost_multiplier_actual_vs_estimated=1.5,
    )
    save_stats(original, str(tmp_path / "project"))
    loaded = load_stats(str(tmp_path / "project"))
    assert loaded.sessions_count == original.sessions_count
    assert loaded.avg_tokens_per_task == original.avg_tokens_per_task
    assert loaded.merge_conflict_rate_by_dir == original.merge_conflict_rate_by_dir
    assert loaded.cost_multiplier_actual_vs_estimated == pytest.approx(1.5)


def test_load_stats_no_project_dir():
    stats = load_stats(None)
    assert isinstance(stats, SessionStats)
