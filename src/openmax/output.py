"""Shared console output for openMax.

Exports a ConsoleProxy that transparently wraps Rich Console. When TUI mode
is active, print/log calls are redirected to DashboardBridge.add_log while
all other Console methods delegate unchanged.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from openmax.tui.bridge import DashboardBridge


class ConsoleProxy:
    """Proxy that delegates to a real Rich Console or captures to a bridge."""

    def __init__(self, real: Console) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_bridge", None)

    def capture_to(self, bridge: DashboardBridge) -> None:
        object.__setattr__(self, "_bridge", bridge)

    def restore(self) -> None:
        object.__setattr__(self, "_bridge", None)

    def print(self, *args: Any, **kwargs: Any) -> None:
        bridge: DashboardBridge | None = object.__getattribute__(self, "_bridge")
        if bridge is not None:
            bridge.add_log(_render_to_str(self._real, *args, **kwargs))
        else:
            self._real.print(*args, **kwargs)

    def log(self, *args: Any, **kwargs: Any) -> None:
        bridge: DashboardBridge | None = object.__getattribute__(self, "_bridge")
        if bridge is not None:
            bridge.add_log(_render_to_str(self._real, *args, **kwargs))
        else:
            self._real.log(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real"), name)


def _render_to_str(real: Console, *args: Any, **kwargs: Any) -> str:
    """Render Rich objects to plain text using a temporary console."""
    buf = io.StringIO()
    temp = Console(file=buf, width=real.width, no_color=True, highlight=False)
    kwargs.pop("style", None)
    kwargs.pop("highlight", None)
    temp.print(*args, **kwargs)
    return buf.getvalue().rstrip("\n")


def enable_tui_capture(bridge: DashboardBridge) -> None:
    """Start redirecting console output to a DashboardBridge."""
    console.capture_to(bridge)


def disable_tui_capture() -> None:
    """Restore normal console output."""
    console.restore()


console: ConsoleProxy = ConsoleProxy(Console())
P = "\u279c"  # ➜
