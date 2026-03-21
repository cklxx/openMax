"""Tests for console capture/redirection to TUI bridge."""

from __future__ import annotations

import io
import threading

from rich.console import Console

from openmax.output import ConsoleProxy, disable_tui_capture, enable_tui_capture
from openmax.tui.bridge import DashboardBridge


def _make_proxy() -> tuple[ConsoleProxy, io.StringIO]:
    buf = io.StringIO()
    real = Console(file=buf, no_color=True, highlight=False)
    return ConsoleProxy(real), buf


def test_print_without_bridge_goes_to_real_console():
    proxy, buf = _make_proxy()
    proxy.print("hello world")
    assert "hello world" in buf.getvalue()


def test_print_with_bridge_goes_to_bridge():
    proxy, buf = _make_proxy()
    bridge = DashboardBridge(goal="test")
    proxy.capture_to(bridge)
    proxy.print("captured line")
    assert buf.getvalue() == ""
    snap = bridge.get_snapshot()
    assert any("captured line" in line for line in snap.log_lines)


def test_log_with_bridge_goes_to_bridge():
    proxy, buf = _make_proxy()
    bridge = DashboardBridge(goal="test")
    proxy.capture_to(bridge)
    proxy.log("logged line")
    assert buf.getvalue() == ""
    snap = bridge.get_snapshot()
    assert any("logged line" in line for line in snap.log_lines)


def test_restore_returns_to_real_console():
    proxy, buf = _make_proxy()
    bridge = DashboardBridge(goal="test")
    proxy.capture_to(bridge)
    proxy.print("captured")
    proxy.restore()
    proxy.print("restored")
    assert "restored" in buf.getvalue()
    assert "captured" not in buf.getvalue()


def test_getattr_delegates_to_real_console():
    proxy, _ = _make_proxy()
    assert isinstance(proxy.width, int)
    assert proxy.width > 0


def test_enable_disable_tui_capture_uses_module_console():
    from openmax.output import console

    bridge = DashboardBridge(goal="test")
    enable_tui_capture(bridge)
    assert object.__getattribute__(console, "_bridge") is bridge
    disable_tui_capture()
    assert object.__getattribute__(console, "_bridge") is None


def test_thread_safety_of_capture():
    proxy, _ = _make_proxy()
    bridge = DashboardBridge(goal="test")
    proxy.capture_to(bridge)
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(20):
                proxy.print(f"thread-{n}-line-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    snap = bridge.get_snapshot()
    assert len(snap.log_lines) == 80


def test_bridge_add_log_truncates():
    bridge = DashboardBridge(goal="test")
    for i in range(2500):
        bridge.add_log(f"line-{i}")
    snap = bridge.get_snapshot()
    assert len(snap.log_lines) == 2000
    assert "line-2499" in snap.log_lines[-1]


def test_existing_import_gets_proxy():
    """Verify that `from openmax.output import console` gets the proxy."""
    from openmax.output import console

    assert isinstance(console, ConsoleProxy)
