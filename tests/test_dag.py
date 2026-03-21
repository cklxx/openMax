"""Tests for tui/dag.py — pure-function DAG renderer."""

from openmax.tui.dag import render_dag


def test_empty_input():
    assert render_dag([], {}) == ""


def test_empty_groups():
    assert render_dag([[], []], {}) == ""


def test_single_task():
    result = render_dag([["build"]], {"build": "done"})
    assert "[build]✓" in result


def test_linear_chain():
    groups = [["research"], ["implement"], ["test"]]
    statuses = {"research": "done", "implement": "running", "test": "pending"}
    result = render_dag(groups, statuses)
    assert "[research]✓" in result
    assert "[implement]●" in result
    assert "[test]○" in result
    assert "│" in result


def test_wide_parallel():
    groups = [["a", "b", "c", "d"]]
    statuses = {"a": "done", "b": "running", "c": "pending", "d": "error"}
    result = render_dag(groups, statuses)
    assert "[a]✓" in result
    assert "[b]●" in result
    assert "[c]○" in result
    assert "[d]✗" in result


def test_diamond_topology():
    groups = [["research"], ["auth", "api"], ["tests"]]
    statuses = {
        "research": "done",
        "auth": "running",
        "api": "running",
        "tests": "pending",
    }
    result = render_dag(groups, statuses)
    assert "[research]✓" in result
    assert "[auth]●" in result
    assert "[api]●" in result
    assert "[tests]○" in result
    lines = result.split("\n")
    assert len(lines) > 3


def test_status_symbols():
    groups = [["a"], ["b"], ["c"], ["d"]]
    statuses = {"a": "pending", "b": "running", "c": "done", "d": "error"}
    result = render_dag(groups, statuses)
    assert "○" in result
    assert "●" in result
    assert "✓" in result
    assert "✗" in result


def test_unknown_status_shows_question_mark():
    result = render_dag([["x"]], {"x": "unknown"})
    assert "[x]?" in result


def test_missing_status_defaults_to_pending():
    result = render_dag([["x"]], {})
    assert "[x]○" in result


def test_fork_has_box_drawing():
    groups = [["top"], ["left", "right"]]
    statuses = {"top": "done", "left": "pending", "right": "pending"}
    result = render_dag(groups, statuses)
    assert "┴" in result


def test_merge_has_box_drawing():
    groups = [["left", "right"], ["bottom"]]
    statuses = {"left": "done", "right": "done", "bottom": "pending"}
    result = render_dag(groups, statuses)
    assert "┬" in result
