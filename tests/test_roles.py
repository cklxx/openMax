"""Tests for adversarial role context builder."""

from __future__ import annotations

from openmax.lead_agent.tools._helpers import _build_role_context


def test_writer_role_returns_empty():
    assert _build_role_context("writer") == ""


def test_reviewer_role_returns_instructions():
    result = _build_role_context("reviewer")
    assert "Reviewer" in result
    assert "Do NOT commit" in result
    assert "critique" in result


def test_reviewer_references_violations_block():
    result = _build_role_context("reviewer")
    assert "{violations_block}" in result


def test_challenger_role_returns_instructions():
    result = _build_role_context("challenger")
    assert "Challenger" in result
    assert "Do NOT modify code" in result
    assert "counter-design" in result


def test_challenger_requires_pseudocode():
    result = _build_role_context("challenger")
    assert "pseudocode" in result
    assert "MANDATORY" in result


def test_debugger_role_returns_instructions():
    result = _build_role_context("debugger")
    assert "Debugger" in result
    assert "root cause" in result


def test_unknown_role_returns_empty():
    assert _build_role_context("unknown") == ""
