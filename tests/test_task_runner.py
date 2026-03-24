"""Tests for multi-task runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from openmax.task_runner import (
    MultiTaskConfig,
    TaskResult,
    _notify_completion,
    _print_summary,
    resolve_task_cwds,
    route_task,
)


def test_route_task_matches_name():
    projects = [{"name": "auth-service", "path": "/code/auth"}]
    assert route_task("fix login in auth-service", projects) == "/code/auth"


def test_route_task_no_match():
    projects = [{"name": "auth-service", "path": "/code/auth"}]
    assert route_task("add pagination to users", projects) is None


def test_route_task_empty_projects():
    assert route_task("any task", []) is None


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
    assert result[1] == ("task2", "/default")  # no project specified, fallback


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


def test_multi_task_config_defaults():
    cfg = MultiTaskConfig(tasks=[("t1", "/a"), ("t2", "/b")])
    assert cfg.concurrency == 6
    assert cfg.no_confirm is True
