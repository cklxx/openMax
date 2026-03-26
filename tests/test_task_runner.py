"""Tests for multi-task runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from openmax.task_runner import (
    MultiTaskConfig,
    TaskResult,
    _notify_completion,
    _print_summary,
    _resolve_concurrency,
    _run_direct_task,
    confirm_tasks,
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
    assert cfg.concurrency == 0  # auto
    assert cfg.direct is True
    assert cfg.stagger_s == 1.0
    assert cfg.no_confirm is True


# --- split_multi_tasks ---


def test_split_numbered_list():
    text = "1. Fix login bug\n2. Add pagination\n3. Write tests"
    result = split_multi_tasks(text)
    assert len(result) == 3
    assert result[0] == "Fix login bug"
    assert result[1] == "Add pagination"
    assert result[2] == "Write tests"


def test_split_numbered_with_parens():
    text = "1) Fix login bug\n2) Add pagination"
    result = split_multi_tasks(text)
    assert len(result) == 2


def test_split_separator():
    text = "Fix login bug\n---\nAdd pagination\n---\nWrite tests"
    result = split_multi_tasks(text)
    assert len(result) == 3
    assert result[0] == "Fix login bug"


def test_split_headings():
    text = "## Fix login bug\ndetails here\n## Add pagination\nmore details"
    result = split_multi_tasks(text)
    assert len(result) == 2
    assert "Fix login bug" in result[0]


def test_split_single_task_returns_original():
    text = "Just one task to do"
    result = split_multi_tasks(text)
    assert result == ["Just one task to do"]


def test_split_empty_lines_filtered():
    text = "1. Task one\n2.   \n3. Task three"
    result = split_multi_tasks(text)
    assert all(t.strip() for t in result)


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


# --- _resolve_concurrency ---


def test_resolve_concurrency_auto():
    cfg = MultiTaskConfig(tasks=[("t", "/a")] * 20, concurrency=0)
    assert _resolve_concurrency(cfg) == 20


def test_resolve_concurrency_auto_capped():
    cfg = MultiTaskConfig(tasks=[("t", "/a")] * 50, concurrency=0)
    assert _resolve_concurrency(cfg) == 30


def test_resolve_concurrency_manual():
    cfg = MultiTaskConfig(tasks=[("t", "/a")] * 20, concurrency=10)
    assert _resolve_concurrency(cfg) == 10


# --- _run_direct_task ---


def test_run_direct_task_success(monkeypatch, tmp_path):
    fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)
    cfg = MultiTaskConfig(tasks=[("t", str(tmp_path))])
    result = _run_direct_task(0, "do something", str(tmp_path), cfg)
    assert result.status == "done"
    assert result.duration_s >= 0


def test_run_direct_task_failure(monkeypatch, tmp_path):
    fake_proc = MagicMock(returncode=1, stdout="", stderr="error msg")
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)
    cfg = MultiTaskConfig(tasks=[("t", str(tmp_path))])
    result = _run_direct_task(0, "do something", str(tmp_path), cfg)
    assert result.status == "failed"
    assert "error msg" in result.error


def test_run_direct_task_exception(monkeypatch, tmp_path):
    def raise_exc(*a, **kw):
        raise OSError("no claude")

    monkeypatch.setattr("openmax.task_runner.subprocess.run", raise_exc)
    cfg = MultiTaskConfig(tasks=[("t", str(tmp_path))])
    result = _run_direct_task(0, "do something", str(tmp_path), cfg)
    assert result.status == "failed"
    assert "no claude" in result.error


# --- stagger ---


def test_submit_staggered_sleeps(monkeypatch):
    from openmax.task_runner import _submit_staggered

    sleep_calls: list[float] = []
    monkeypatch.setattr("openmax.task_runner.time.sleep", lambda s: sleep_calls.append(s))
    fake_proc = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("openmax.task_runner.subprocess.run", lambda *a, **kw: fake_proc)

    from concurrent.futures import ThreadPoolExecutor

    cfg = MultiTaskConfig(
        tasks=[("t1", "/a"), ("t2", "/b"), ("t3", "/c")],
        stagger_s=0.5,
        direct=True,
    )
    ui = MagicMock()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = _submit_staggered(pool, cfg, ui)
        assert len(futures) == 3
    assert len(sleep_calls) == 2  # N-1 sleeps
    assert all(s == 0.5 for s in sleep_calls)
