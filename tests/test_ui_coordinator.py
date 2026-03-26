"""Tests for UICoordinator — serialized multi-task I/O."""

from __future__ import annotations

import threading

from openmax.ui_coordinator import UICoordinator


def test_print_banner_shows_all_tasks(capsys):
    ui = UICoordinator(tasks=["fix login", "add pagination"])
    ui.print_banner("batch-123")
    out = capsys.readouterr().out
    assert "openMax" in out
    assert "2 tasks" in out
    assert "fix login" in out
    assert "add pagination" in out


def test_request_input_returns_prompt_result():
    ui = UICoordinator(tasks=["task1"])
    result = ui.request_input("task1", lambda: "user_answer")
    assert result == "user_answer"


def test_request_input_serializes_concurrent_calls():
    """Two threads calling request_input should not interleave."""
    ui = UICoordinator(tasks=["a", "b"])
    order: list[str] = []
    import time

    def slow_prompt(label: str) -> str:
        order.append(f"{label}_start")
        time.sleep(0.05)
        order.append(f"{label}_end")
        return label

    t1 = threading.Thread(target=lambda: ui.request_input("a", lambda: slow_prompt("a")))
    t2 = threading.Thread(target=lambda: ui.request_input("b", lambda: slow_prompt("b")))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # With lock, starts and ends are paired (no interleaving)
    assert len(order) == 4
    assert order[0].endswith("_start")
    assert order[1].endswith("_end")
    assert order[0][0] == order[1][0]  # same task label
