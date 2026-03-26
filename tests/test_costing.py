"""Tests for cost estimation module."""

from __future__ import annotations

from openmax.lead_agent.tools._costing import (
    MODEL_PRICING,
    estimate_task_cost,
)


def test_estimate_cost_uses_model_pricing():
    result = estimate_task_cost(4000, "claude-code")
    expected_cost = 3000 * MODEL_PRICING["claude-code"] / 1_000_000
    assert result.estimated_cost_usd == round(expected_cost, 6)


def test_estimate_cost_codex_pricing():
    result = estimate_task_cost(4000, "codex")
    expected_cost = 3000 * MODEL_PRICING["codex"] / 1_000_000
    assert result.estimated_cost_usd == round(expected_cost, 6)


def test_estimate_unknown_agent_uses_default():
    result = estimate_task_cost(4000, "unknown-agent")
    expected_cost = 3000 * 9.0 / 1_000_000
    assert result.estimated_cost_usd == round(expected_cost, 6)


def test_estimate_zero_prompt_len():
    result = estimate_task_cost(0, "claude-code")
    assert result.estimated_input_tokens == 1
    assert result.estimated_output_tokens == 2
    assert result.estimated_tokens == 3


def test_estimate_large_prompt():
    result = estimate_task_cost(400_000, "claude-code")
    assert result.estimated_input_tokens == 100_000
    assert result.estimated_output_tokens == 200_000
    assert result.estimated_cost_usd > 0
