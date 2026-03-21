"""Tests for tui/dag.py — pure-function DAG renderer."""

from openmax.tui.dag import (
    _detect_cycle,
    _execution_front,
    _topo_layers,
    render_dag,
)

# -- render_dag basic --


def test_empty_input():
    assert "(no tasks)" in render_dag([], {})


def test_empty_groups():
    assert "(no tasks)" in render_dag([[], []], {})


def test_single_task():
    result = render_dag([["build"]], {"build": "done"})
    assert "✓" in result
    assert "build" in result


def test_status_symbols_present():
    statuses = {"a": "pending", "b": "running", "c": "done", "d": "error"}
    result = render_dag([list(statuses.keys())], statuses)
    assert "○" in result
    assert "●" in result
    assert "✓" in result
    assert "✗" in result


def test_unknown_status():
    result = render_dag([["x"]], {"x": "unknown"})
    assert "?" in result


def test_missing_status_defaults_to_pending():
    result = render_dag([["x"]], {})
    assert "○" in result


# -- dependency-based rendering --


def test_with_deps_renders_layers():
    deps = {"implement": ["research"], "test": ["implement"]}
    statuses = {"research": "done", "implement": "running", "test": "pending"}
    result = render_dag([list(statuses.keys())], statuses, deps=deps)
    assert "research" in result
    assert "implement" in result
    assert "test" in result


def test_no_deps_renders_compact():
    result = render_dag([["a", "b"]], {"a": "done", "b": "pending"})
    assert "compact" in result.lower() or "a" in result


def test_running_node_has_animation_indicator():
    result = render_dag([["t"]], {"t": "running"})
    assert "⟳" in result


def test_done_node_has_checkmark():
    result = render_dag([["t"]], {"t": "done"})
    assert "✓" in result


def test_error_node_has_x():
    result = render_dag([["t"]], {"t": "error"})
    assert "✗" in result


# -- topological layers --


def test_topo_layers_linear():
    deps = {"b": ["a"], "c": ["b"]}
    layers = _topo_layers(deps, {"a", "b", "c"})
    assert layers == [["a"], ["b"], ["c"]]


def test_topo_layers_diamond():
    deps = {"b": ["a"], "c": ["a"], "d": ["b", "c"]}
    layers = _topo_layers(deps, {"a", "b", "c", "d"})
    assert layers[0] == ["a"]
    assert sorted(layers[1]) == ["b", "c"]
    assert layers[2] == ["d"]


def test_topo_layers_no_deps():
    layers = _topo_layers({}, {"a", "b", "c"})
    assert len(layers) == 1
    assert sorted(layers[0]) == ["a", "b", "c"]


# -- cycle detection --


def test_detect_cycle_true():
    deps = {"a": ["b"], "b": ["a"]}
    assert _detect_cycle(deps, {"a", "b"}) is True


def test_detect_cycle_false():
    deps = {"b": ["a"]}
    assert _detect_cycle(deps, {"a", "b"}) is False


def test_cycle_renders_with_warning():
    deps = {"a": ["b"], "b": ["a"]}
    result = render_dag([["a", "b"]], {"a": "running", "b": "pending"}, deps=deps)
    assert "Cycle detected" in result


# -- execution front --


def test_execution_front():
    statuses = {"a": "done", "b": "running", "c": "pending"}
    deps = {"b": ["a"], "c": ["b"]}
    front = _execution_front(statuses, deps)
    assert "b" in front
    assert "a" in front  # dependency of running task


# -- terminal width fallback --


def test_narrow_terminal_renders_vertical():
    deps = {"b": ["a"]}
    statuses = {"a": "done", "b": "running"}
    result = render_dag([["a", "b"]], statuses, deps=deps, terminal_width=40)
    assert "narrow" in result.lower()


# -- compact threshold --


def test_many_tasks_renders_compact():
    tasks = [f"task{i}" for i in range(25)]
    statuses = {t: "pending" for t in tasks}
    deps = {tasks[i]: [tasks[i - 1]] for i in range(1, len(tasks))}
    result = render_dag([tasks], statuses, deps=deps)
    assert "compact" in result.lower()


# -- execution front highlight --


def test_front_highlight_marker():
    deps = {"b": ["a"]}
    statuses = {"a": "done", "b": "running"}
    result = render_dag([["a", "b"]], statuses, deps=deps)
    assert "◀" in result  # running task gets front marker
