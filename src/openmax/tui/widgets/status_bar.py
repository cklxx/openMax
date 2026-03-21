"""Status bar widget showing phase, elapsed time, and task counts."""

from __future__ import annotations

import time

from textual.widgets import Static

from openmax.formatting import estimate_cost_usd, format_cost, format_tokens_short
from openmax.tui.bridge import DashboardState

_BLOCK_FULL = "\u2588"
_BLOCK_LIGHT = "\u2591"


def _phase_bar(phase: str, pct: int | None) -> str:
    if pct is None:
        return phase
    filled = pct // 5
    return f"{phase} {_BLOCK_FULL * filled}{_BLOCK_LIGHT * (20 - filled)} {pct}%"


def _task_counts(state: DashboardState) -> str:
    total = len(state.subtasks)
    done = errors = 0
    for t in state.subtasks.values():
        if t.status == "done":
            done += 1
        elif t.status == "error":
            errors += 1
    return f"{done}/{total} done  {errors} err"


class StatusBarWidget(Static):
    """Bottom status bar showing phase, elapsed, task counts."""

    DEFAULT_CSS = """
    StatusBarWidget {
        width: 100%;
        height: 3;
        dock: bottom;
        padding: 0 1;
        background: $surface;
    }
    """

    def refresh_from_state(self, state: DashboardState) -> None:
        elapsed = time.monotonic() - state.start_time
        phase = _phase_bar(state.phase, state.phase_pct)
        counts = _task_counts(state)
        tokens = state.total_input_tokens + state.total_output_tokens
        tok_str = format_tokens_short(tokens)
        cost = estimate_cost_usd(state.total_input_tokens, state.total_output_tokens)
        cost_str = format_cost(cost)
        self.update(f"{phase}  {elapsed:.0f}s  {counts}  \u2b07 {tok_str} tokens \u00b7 {cost_str}")
