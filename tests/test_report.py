"""Tests for openmax.lead_agent.tools._report — cost anomaly detection."""

from __future__ import annotations

from openmax.lead_agent.tools._report import (
    _ANOMALY_CEILING,
    _ANOMALY_FLOOR,
    _STATIC_THRESHOLD,
    detect_cost_anomaly,
)
from openmax.stats import SessionStats

# --- detect_cost_anomaly ---


def test_no_alert_when_ratio_below_threshold():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=1.0)
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=2000, stats=stats)
    assert result is None


def test_alert_when_ratio_exceeds_static_threshold():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=1.0)
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=4000, stats=stats)
    assert result is not None
    assert result["alert"] is True
    assert result["actual_vs_estimated"] == 4.0
    assert result["threshold"] == _STATIC_THRESHOLD


def test_no_alert_at_exactly_threshold():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=1.0)
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=3000, stats=stats)
    assert result is None


def test_skip_when_estimated_zero():
    stats = SessionStats()
    result = detect_cost_anomaly(estimated_tokens=0, actual_tokens=5000, stats=stats)
    assert result is None


def test_skip_when_estimated_negative():
    stats = SessionStats()
    result = detect_cost_anomaly(estimated_tokens=-100, actual_tokens=5000, stats=stats)
    assert result is None


def test_historical_raises_threshold():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=4.0)
    # historical * 2.0 = 8.0 > static 3.0, so threshold = 8.0
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=7000, stats=stats)
    assert result is None  # 7x < 8.0 threshold


def test_historical_triggers_above_raised_threshold():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=4.0)
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=9000, stats=stats)
    assert result is not None
    assert result["threshold"] == 8.0


def test_threshold_clamped_to_floor():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=0.5)
    # historical * 2.0 = 1.0, static = 3.0 → max = 3.0
    # clamp(3.0, 1.5, 10.0) = 3.0 (floor doesn't bite here)
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=3500, stats=stats)
    assert result is not None
    assert result["threshold"] >= _ANOMALY_FLOOR


def test_threshold_clamped_to_ceiling():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=5.0)
    # historical * 2.0 = 10.0, clamped to ceiling
    result = detect_cost_anomaly(estimated_tokens=1000, actual_tokens=10500, stats=stats)
    assert result is not None
    assert result["threshold"] == _ANOMALY_CEILING


def test_alert_message_contains_ratio():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=1.0)
    result = detect_cost_anomaly(estimated_tokens=100, actual_tokens=500, stats=stats)
    assert result is not None
    assert "5.0x" in result["message"]


def test_no_alert_when_actual_below_estimated():
    stats = SessionStats(cost_multiplier_actual_vs_estimated=1.0)
    result = detect_cost_anomaly(estimated_tokens=5000, actual_tokens=1000, stats=stats)
    assert result is None
