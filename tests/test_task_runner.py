"""Tests for multi-task runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from openmax.task_runner import (
    MultiTaskConfig,
    TaskResult,
    _notify_completion,
    _print_summary,
    confirm_tasks,
    format_batch_prompt,
    resolve_task_cwds,
    route_task,
    split_multi_tasks,
)

# --- route_task ---


def test_route_task_matches_name():
    projects = [{"name": "auth-service", "path": "/code/auth"}]
    assert route_task("fix login in auth-service", projects) == "/code/auth"


def test_route_task_no_match():
    projects = [{"name": "auth-service", "path": "/code/auth"}]
    assert route_task("add pagination to users", projects) is None


def test_route_task_empty_projects():
    assert route_task("any task", []) is None


# --- resolve_task_cwds ---


def test_resolve_task_cwds_with_projects(monkeypatch):
    monkeypatch.setattr(
        "openmax.task_runner.list_projects",
        lambda: [{"name": "auth", "path": "/code/auth"}],
    )
    monkeypatch.setattr(
        "openmax.task_runner.find_project",
        lambda name: "/code/auth" if name == "auth" else None,
    )

    result = resolve_task_cwds(("task1", "task2"), ("auth",), "/default")
    assert result[0] == ("task1", "/code/auth")
    assert result[1] == ("task2", "/default")


def test_resolve_task_cwds_with_routing(monkeypatch):
    monkeypatch.setattr(
        "openmax.task_runner.list_projects",
        lambda: [{"name": "gateway", "path": "/code/gw"}],
    )
    monkeypatch.setattr("openmax.task_runner.find_project", lambda name: None)

    result = resolve_task_cwds(("fix gateway routing",), (), "/default")
    assert result[0] == ("fix gateway routing", "/code/gw")


def test_resolve_task_cwds_fallback_to_cwd(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.list_projects", lambda: [])
    monkeypatch.setattr("openmax.task_runner.find_project", lambda name: None)

    result = resolve_task_cwds(("task1",), (), "/default")
    assert result[0] == ("task1", "/default")


# --- _print_summary / _notify_completion ---


def test_print_summary_no_crash(capsys):
    results = [
        TaskResult(task="task1", cwd="/a", status="done", duration_s=10.0),
        TaskResult(task="task2", cwd="/b", status="failed", duration_s=5.0, error="boom"),
    ]
    _print_summary(results)


def test_notify_completion_non_darwin(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.sys.platform", "linux")
    _notify_completion([TaskResult(task="t", cwd="/a", status="done")])


def test_notify_completion_darwin(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.sys.platform", "darwin")
    mock_run = MagicMock()
    monkeypatch.setattr("openmax.task_runner.subprocess.run", mock_run)
    _notify_completion([TaskResult(task="t", cwd="/a", status="done")])
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "osascript" in cmd


# --- MultiTaskConfig defaults ---


def test_multi_task_config_defaults():
    cfg = MultiTaskConfig(tasks=[("t1", "/a"), ("t2", "/b")])
    assert cfg.concurrency == 6
    assert cfg.no_confirm is True


# --- split_multi_tasks ---


def test_split_single_task_returns_original():
    text = "Just one task to do"
    result = split_multi_tasks(text)
    assert result == ["Just one task to do"]


def test_split_short_text_skips_llm():
    """Short text below LLM_MIN_LENGTH returns as-is without LLM call."""
    result = split_multi_tasks("short task")
    assert result == ["short task"]


def test_split_delegates_to_llm(monkeypatch):
    """Long text delegates to LLM for decomposition."""
    fake_proc = MagicMock(
        returncode=0,
        stdout='["Fix login bug", "Add pagination"]',
    )
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)

    long_text = "Fix the login bug in auth module and also add pagination to users endpoint " * 2
    result = split_multi_tasks(long_text)
    assert len(result) == 2
    assert result[0] == "Fix login bug"


def test_split_llm_single_task_returns_original(monkeypatch):
    """LLM returning single-element array means input stays as-is."""
    long_text = "Refactor the entire authentication module to use JWT tokens " * 2
    fake_proc = MagicMock(
        returncode=0,
        stdout='["Refactor the entire authentication module to use JWT tokens"]',
    )
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)

    result = split_multi_tasks(long_text)
    assert result == [long_text.strip()]


# --- LLM split fallback ---


def test_split_via_llm_parses_json(monkeypatch):
    """LLM fallback parses JSON array from claude CLI output."""
    from openmax.task_runner import _split_via_llm

    fake_proc = MagicMock(
        returncode=0,
        stdout='["Fix login", "Add pagination", "Write tests"]',
    )
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)

    result = _split_via_llm("x" * 100)
    assert len(result) == 3
    assert result[0] == "Fix login"


def test_split_via_llm_handles_markdown_fence(monkeypatch):
    """LLM output with markdown code fence is correctly parsed."""
    from openmax.task_runner import _split_via_llm

    fake_proc = MagicMock(
        returncode=0,
        stdout='```json\n["Task A", "Task B"]\n```',
    )
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)

    result = _split_via_llm("x" * 100)
    assert len(result) == 2


def test_split_via_llm_fallback_on_error(monkeypatch):
    """LLM failure falls back to empty list."""
    from openmax.task_runner import _split_via_llm

    def raise_exc(*a, **kw):
        raise OSError("no claude")

    monkeypatch.setattr("openmax.task_runner.subprocess.run", raise_exc)

    result = _split_via_llm("x" * 100)
    assert result == []


def test_split_via_llm_skipped_for_short_text():
    """Short text doesn't trigger LLM call."""
    result = split_multi_tasks("short task")
    assert result == ["short task"]


# --- confirm_tasks ---


def test_confirm_tasks_yes(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.console.input", lambda _: "y")
    assert confirm_tasks(["task1", "task2"]) is True


def test_confirm_tasks_empty_confirms(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.console.input", lambda _: "")
    assert confirm_tasks(["task1", "task2"]) is True


def test_confirm_tasks_no(monkeypatch):
    monkeypatch.setattr("openmax.task_runner.console.input", lambda _: "n")
    assert confirm_tasks(["task1", "task2"]) is False


# --- format_batch_prompt ---


def test_format_batch_prompt_structure():
    result = format_batch_prompt(["Fix login", "Add pagination"])
    assert "2 INDEPENDENT tasks" in result
    assert "1. Fix login" in result
    assert "2. Add pagination" in result
    assert "parallel" in result.lower()


def test_format_batch_prompt_many_tasks():
    tasks = [f"Task {i}" for i in range(20)]
    result = format_batch_prompt(tasks)
    assert "20 INDEPENDENT tasks" in result
    assert "20. Task 19" in result
