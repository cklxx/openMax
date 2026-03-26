"""Tests for openmax.formatting shared utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openmax.formatting import (
    estimate_cost_usd,
    format_cost,
    format_relative_time,
    format_tokens,
    format_tokens_short,
    status_icon,
    status_icon_plain,
)


class TestFormatRelativeTime:
    def test_none_returns_dash(self):
        assert format_relative_time(None) == "-"

    def test_empty_string_returns_dash(self):
        assert format_relative_time("") == "-"

    def test_invalid_iso_returns_raw(self):
        assert format_relative_time("not-a-date") == "not-a-date"

    def test_just_now(self):
        now = datetime.now(timezone.utc).isoformat()
        assert format_relative_time(now) == "just now"

    def test_minutes_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=15)
        assert format_relative_time(dt.isoformat()) == "15m ago"

    def test_hours_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert format_relative_time(dt.isoformat()) == "3h ago"

    def test_yesterday(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=30)
        assert format_relative_time(dt.isoformat()) == "yesterday"

    def test_days_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(days=4)
        assert format_relative_time(dt.isoformat()) == "4d ago"

    def test_older_shows_date(self):
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        result = format_relative_time(dt.isoformat())
        # Should be like "Feb 19" — a month abbreviation + day
        assert len(result) >= 4
        assert result != "-"


class TestFormatTokens:
    def test_thousands(self):
        assert format_tokens(1_234) == "1.2k"

    def test_millions(self):
        assert format_tokens(1_500_000) == "1.5M"

    def test_none_returns_dash(self):
        assert format_tokens(None) == "-"


class TestFormatCost:
    def test_small_cost_three_decimals(self):
        assert format_cost(0.005) == "$0.005"

    def test_normal_cost_two_decimals(self):
        assert format_cost(1.234) == "$1.23"


class TestFormatTokensShort:
    def test_thousands(self):
        assert format_tokens_short(12500) == "12.5k"

    def test_millions(self):
        assert format_tokens_short(2_500_000) == "2.5M"


class TestEstimateCostUsd:
    def test_zero_tokens(self):
        assert estimate_cost_usd(0, 0) == 0.0

    def test_input_only(self):
        cost = estimate_cost_usd(1_000_000, 0)
        assert cost > 0
        assert abs(cost - 3.0) < 0.01

    def test_output_more_expensive(self):
        input_cost = estimate_cost_usd(1000, 0)
        output_cost = estimate_cost_usd(0, 1000)
        assert output_cost > input_cost


class TestStatusIcon:
    def test_done_green(self):
        result = status_icon("done")
        assert "✔" in result
        assert "green" in result

    def test_error_red(self):
        result = status_icon("error")
        assert "✘" in result
        assert "red" in result

    def test_running_cyan(self):
        result = status_icon("running")
        assert "●" in result
        assert "cyan" in result

    def test_pending_dim(self):
        result = status_icon("pending")
        assert "○" in result

    def test_none_returns_dim_circle(self):
        result = status_icon(None)
        assert "○" in result

    def test_unknown_returns_circle(self):
        result = status_icon("unknown-status")
        assert "○" in result

    def test_case_insensitive(self):
        result = status_icon("DONE")
        assert "✔" in result

    def test_plain_no_markup(self):
        assert status_icon_plain("done") == "✔"
        assert status_icon_plain("error") == "✘"
        assert status_icon_plain(None) == "○"
