"""Tests for the benchmark module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from openmax.benchmark.report import print_report, save_report
from openmax.benchmark.runner import (
    BenchmarkReport,
    BenchmarkResult,
    TaskComparison,
    _create_workspace,
    _parse_claude_json_usage,
    _verify,
    run_benchmark,
)
from openmax.benchmark.tasks import BenchmarkTask, load_task, load_task_suite

# ---------------------------------------------------------------------------
# Unit: BenchmarkTask loading
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_task_yaml(tmp_path: Path) -> Path:
    data = {
        "id": "test-task",
        "name": "Test Task",
        "difficulty": "small",
        "prompt": "Do something in {workspace}",
        "setup_script": "echo setup",
        "verify_script": "echo passed",
        "success_pattern": "passed",
        "timeout_seconds": 60,
        "tags": ["test"],
    }
    p = tmp_path / "test-task.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def test_load_task(sample_task_yaml: Path) -> None:
    task = load_task(sample_task_yaml)
    assert task.id == "test-task"
    assert task.difficulty == "small"
    assert "{workspace}" in task.prompt
    assert task.timeout_seconds == 60


def test_load_task_suite(sample_task_yaml: Path) -> None:
    suite = load_task_suite(sample_task_yaml.parent)
    assert len(suite) == 1
    assert suite[0].id == "test-task"


def test_load_task_suite_sorted_by_difficulty(tmp_path: Path) -> None:
    for diff in ("large", "small", "medium"):
        data = {
            "id": f"task-{diff}",
            "name": f"Task {diff}",
            "difficulty": diff,
            "prompt": "x",
            "verify_script": "true",
            "success_pattern": "x",
        }
        (tmp_path / f"task-{diff}.yaml").write_text(yaml.dump(data), encoding="utf-8")
    suite = load_task_suite(tmp_path)
    assert [t.difficulty for t in suite] == ["small", "medium", "large"]


def test_load_builtin_suite() -> None:
    suite = load_task_suite()
    assert len(suite) >= 3
    assert all(isinstance(t, BenchmarkTask) for t in suite)


# ---------------------------------------------------------------------------
# Unit: BenchmarkResult / TaskComparison
# ---------------------------------------------------------------------------


def test_task_comparison_speedup() -> None:
    cc = BenchmarkResult(task_id="t", mode="claude-code", duration_seconds=60.0, success=True)
    om = BenchmarkResult(task_id="t", mode="openmax", duration_seconds=20.0, success=True)
    comp = TaskComparison(
        task_id="t",
        task_name="T",
        difficulty="small",
        claude_code=cc,
        openmax=om,
    )
    assert comp.speedup == pytest.approx(3.0)


def test_task_comparison_no_openmax() -> None:
    cc = BenchmarkResult(task_id="t", mode="claude-code", duration_seconds=60.0)
    comp = TaskComparison(task_id="t", task_name="T", difficulty="small", claude_code=cc)
    assert comp.speedup is None


def test_benchmark_report_avg_speedup() -> None:
    comps = [
        TaskComparison(
            task_id="a",
            task_name="A",
            difficulty="small",
            claude_code=BenchmarkResult(task_id="a", mode="cc", duration_seconds=60),
            openmax=BenchmarkResult(task_id="a", mode="om", duration_seconds=20),
        ),
        TaskComparison(
            task_id="b",
            task_name="B",
            difficulty="medium",
            claude_code=BenchmarkResult(task_id="b", mode="cc", duration_seconds=40),
            openmax=BenchmarkResult(task_id="b", mode="om", duration_seconds=20),
        ),
    ]
    report = BenchmarkReport(comparisons=comps)
    assert report.avg_speedup == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Unit: workspace creation and verification
# ---------------------------------------------------------------------------


def test_create_workspace() -> None:
    task = BenchmarkTask(
        id="ws-test",
        name="WS",
        difficulty="small",
        prompt="x",
        setup_script="echo hello > file.txt",
        verify_script="true",
        success_pattern="x",
    )
    ws = _create_workspace(task)
    try:
        assert (ws / "file.txt").exists()
        result = subprocess.run(["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True)
        assert "setup" in result.stdout
    finally:
        import shutil

        shutil.rmtree(ws, ignore_errors=True)


def test_verify_success() -> None:
    task = BenchmarkTask(
        id="v",
        name="V",
        difficulty="small",
        prompt="x",
        setup_script="",
        verify_script="echo 'all tests passed'",
        success_pattern="passed",
    )
    assert _verify(task, Path("/tmp")) is True


def test_verify_failure() -> None:
    task = BenchmarkTask(
        id="v",
        name="V",
        difficulty="small",
        prompt="x",
        setup_script="",
        verify_script="echo 'FAILED'",
        success_pattern="passed",
    )
    assert _verify(task, Path("/tmp")) is False


# ---------------------------------------------------------------------------
# Unit: Claude JSON output parsing
# ---------------------------------------------------------------------------


def test_parse_claude_json_usage_valid() -> None:
    raw = '{"usage": {"input_tokens": 100, "output_tokens": 50}, "cost_usd": 0.005}'
    result = _parse_claude_json_usage(raw)
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["cost_usd"] == 0.005


def test_parse_claude_json_usage_invalid() -> None:
    result = _parse_claude_json_usage("not json")
    assert result["input_tokens"] == 0


# ---------------------------------------------------------------------------
# Unit: report
# ---------------------------------------------------------------------------


def test_print_report_no_crash() -> None:
    report = BenchmarkReport(comparisons=[], model="test", timestamp="2024-01-01")
    print_report(report)


def test_save_report(tmp_path: Path) -> None:
    report = BenchmarkReport(comparisons=[], model="test", timestamp="2024-01-01T00:00:00")
    path = save_report(report, output_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".json"


# ---------------------------------------------------------------------------
# Integration: run_benchmark with mocked executors
# ---------------------------------------------------------------------------


def test_run_benchmark_mocked(tmp_path: Path) -> None:
    task = BenchmarkTask(
        id="mock-test",
        name="Mock Test",
        difficulty="small",
        prompt="do something in {workspace}",
        setup_script="echo init > README.md",
        verify_script="echo passed",
        success_pattern="passed",
        timeout_seconds=30,
    )

    fake_cc_result = BenchmarkResult(
        task_id="mock-test",
        mode="claude-code",
        duration_seconds=10.0,
        success=True,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    )
    fake_om_result = BenchmarkResult(
        task_id="mock-test",
        mode="openmax",
        duration_seconds=5.0,
        success=True,
        input_tokens=200,
        output_tokens=100,
        cost_usd=0.02,
        num_subtasks=3,
    )

    with (
        patch("openmax.benchmark.runner._run_claude_code", return_value=fake_cc_result),
        patch("openmax.benchmark.runner._run_openmax", return_value=fake_om_result),
    ):
        report = run_benchmark([task])

    assert len(report.comparisons) == 1
    assert report.comparisons[0].speedup == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# CLI: benchmark list
# ---------------------------------------------------------------------------


def test_cli_benchmark_list() -> None:
    from openmax.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["benchmark", "list"])
    assert result.exit_code == 0
    assert "add-rest-endpoi" in result.output


def test_cli_benchmark_run_no_tasks(tmp_path: Path) -> None:
    from openmax.cli import main

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["benchmark", "run", "--tasks", str(empty_dir)])
    assert result.exit_code != 0
    assert "No benchmark tasks" in result.output
