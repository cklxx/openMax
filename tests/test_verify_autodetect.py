"""Tests for run_verification auto-detection from project tooling."""

from __future__ import annotations

import json
from types import SimpleNamespace

import anyio

from openmax.agent_registry import built_in_agent_registry
from openmax.lead_agent import PlanResult
from openmax.lead_agent import tools as lead_agent_tools
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.session_runtime import SessionStore
from tests.conftest import patch_time as _patch_time


class DummyPaneManager:
    def __init__(self) -> None:
        self._text = ""
        self._pane_counter = 100

    def create_window(self, command, purpose, agent_type, title, cwd, env=None):
        self._pane_counter += 1
        return SimpleNamespace(pane_id=self._pane_counter, window_id=7)

    def add_pane(self, window_id, command, purpose, agent_type, cwd, env=None, **kw):
        self._pane_counter += 1
        return SimpleNamespace(pane_id=self._pane_counter, window_id=window_id)

    def get_text(self, pane_id) -> str:
        return self._text

    def is_pane_alive(self, pane_id) -> bool:
        return False


def _setup(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    meta = store.create_session("verify-test", "Goal", str(tmp_path))
    runtime = LeadAgentRuntime(
        cwd=str(tmp_path),
        plan=PlanResult(goal="Goal"),
        pane_mgr=DummyPaneManager(),
        session_store=store,
        session_meta=meta,
        agent_registry=built_in_agent_registry(),
        plan_confirm=False,
    )
    token = bind_lead_agent_runtime(runtime)
    return runtime, token


def _teardown(token):
    reset_lead_agent_runtime(token)


def _parse(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


# ── Explicit command still works (backward compat) ──────────────────────


def test_explicit_command_overrides_autodetect(monkeypatch, tmp_path):
    """When command is provided, auto-detection is skipped."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)
    runtime.pane_mgr._text = "OK\n__OPENMAX_EXIT_0__\n"

    # Create a pyproject.toml so auto-detect would find something
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest]\n")

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "command": "my-custom-lint", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "pass"
    assert parsed["command"] == "my-custom-lint"
    assert "language" not in parsed
    _teardown(token)


# ── Single language auto-detect ─────────────────────────────────────────


def test_autodetect_single_python_lint(monkeypatch, tmp_path):
    """Auto-detects Python lint command when no command provided."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)
    runtime.pane_mgr._text = "All good\n__OPENMAX_EXIT_0__\n"

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "pass"
    assert "ruff" in parsed["command"]
    assert parsed["language"] == "python"
    _teardown(token)


def test_autodetect_single_python_test(monkeypatch, tmp_path):
    """Auto-detects Python test command when no command provided."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)
    runtime.pane_mgr._text = "1 passed\n__OPENMAX_EXIT_0__\n"

    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "test", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "pass"
    assert "pytest" in parsed["command"]
    assert parsed["language"] == "python"
    _teardown(token)


# ── Multi-language auto-detect ──────────────────────────────────────────


def test_autodetect_multi_lang_lint(monkeypatch, tmp_path):
    """Auto-detects lint for both Python and Go."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)
    runtime.pane_mgr._text = "OK\n__OPENMAX_EXIT_0__\n"

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "go.mod").write_text("module example\n")

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "pass"
    assert "results" in parsed
    assert len(parsed["results"]) == 2
    langs = {r["language"] for r in parsed["results"]}
    assert langs == {"python", "go"}
    _teardown(token)


def test_autodetect_multi_lang_partial_fail(monkeypatch, tmp_path):
    """Multi-language: overall fails if any language fails."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)

    call_count = 0

    def _alternating_text(pane_id: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return "OK\n__OPENMAX_EXIT_0__\n"
        return "error\n__OPENMAX_EXIT_1__\n"

    runtime.pane_mgr.get_text = _alternating_text

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "go.mod").write_text("module example\n")

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "fail"
    assert len(parsed["results"]) == 2
    assert "dispatch_hint" in parsed
    _teardown(token)


# ── No tooling detected ────────────────────────────────────────────────


def test_autodetect_no_tooling_returns_error(monkeypatch, tmp_path):
    """Returns error when no command and no tooling detected."""
    runtime, token = _setup(tmp_path)
    _patch_time(monkeypatch)

    result = anyio.run(
        lead_agent_tools.run_verification.handler,
        {"check_type": "lint", "timeout": 30},
    )
    parsed = _parse(result)
    assert parsed["status"] == "error"
    assert "no tooling" in parsed["error"].lower() or "no verification" in parsed["error"].lower()
    _teardown(token)


# ── Unit tests for helper functions ─────────────────────────────────────


def test_resolve_commands_explicit():
    """_resolve_commands returns explicit command unchanged."""
    from openmax.lead_agent.tools._verify import _resolve_commands

    result = _resolve_commands("/nonexistent", "lint", "my-cmd")
    assert result == [("my-cmd", None)]


def test_resolve_commands_autodetect(tmp_path):
    """_resolve_commands auto-detects from project files."""
    from openmax.lead_agent.tools._verify import _resolve_commands

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest]\n")
    result = _resolve_commands(str(tmp_path), "lint", None)
    assert len(result) == 1
    assert "ruff" in result[0][0]
    assert result[0][1] == "python"


def test_resolve_commands_multi_lang(tmp_path):
    """_resolve_commands returns commands for all detected languages."""
    from openmax.lead_agent.tools._verify import _resolve_commands

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"\n')
    result = _resolve_commands(str(tmp_path), "lint", None)
    assert len(result) == 2
    langs = {lang for _, lang in result}
    assert "python" in langs
    assert "rust" in langs


def test_cmd_for_check_type():
    """_cmd_for_check_type extracts correct command per check type."""
    from openmax.lead_agent.tools._verify import _cmd_for_check_type
    from openmax.project_tools import ProjectTooling

    t = ProjectTooling(lint_cmd="lint-cmd", test_cmd="test-cmd", language="python")
    assert _cmd_for_check_type(t, "lint") == "lint-cmd"
    assert _cmd_for_check_type(t, "test") == "test-cmd"
    assert _cmd_for_check_type(t, "format") == "lint-cmd"
    assert _cmd_for_check_type(t, "custom") == "lint-cmd"
