"""Tool for reporting completion with cost anomaly detection."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _record_phase_anchor,
    _tool_response,
)
from openmax.stats import SessionStats

_ANOMALY_FLOOR = 1.5
_ANOMALY_CEILING = 10.0
_STATIC_THRESHOLD = 3.0
_HISTORICAL_MULTIPLIER = 2.0


def detect_cost_anomaly(
    estimated_tokens: int,
    actual_tokens: int,
    stats: SessionStats,
) -> dict[str, Any] | None:
    """Detect if actual cost significantly exceeds estimate."""
    if estimated_tokens <= 0:
        return None
    ratio = actual_tokens / estimated_tokens
    historical = stats.cost_multiplier_actual_vs_estimated
    raw_threshold = max(_STATIC_THRESHOLD, historical * _HISTORICAL_MULTIPLIER)
    threshold = max(_ANOMALY_FLOOR, min(_ANOMALY_CEILING, raw_threshold))
    if ratio <= threshold:
        return None
    return {
        "alert": True,
        "actual_vs_estimated": round(ratio, 2),
        "threshold": round(threshold, 2),
        "message": f"Cost anomaly: {ratio:.1f}x estimated (threshold: {threshold:.1f}x)",
    }


def _aggregate_session_tokens() -> tuple[int, int]:
    """Sum estimated and actual tokens across all subtasks. Returns (estimated, actual)."""
    try:
        from openmax.lead_agent.runtime import get_lead_agent_runtime

        rt = get_lead_agent_runtime()
        if rt.plan is None:
            return 0, 0
        estimated = sum(st.token_budget or 0 for st in rt.plan.subtasks)
        actual = sum(st.tokens_used for st in rt.plan.subtasks)
        return estimated, actual
    except Exception:
        return 0, 0


def _persist_session_stats() -> None:
    """Best-effort save of updated session stats."""
    try:
        from openmax.lead_agent.runtime import get_lead_agent_runtime
        from openmax.stats import save_stats, update_stats

        rt = get_lead_agent_runtime()
        if rt.session_stats is not None:
            rt.session_stats = update_stats(rt.session_stats, rt.token_usage)
            save_stats(rt.session_stats, rt.cwd)
    except Exception:
        pass


@tool(
    "report_completion",
    "Report final completion. Call once when all tasks are done. "
    "Describe what was delivered, not attempted.",
    {"completion_pct": int, "notes": str},
)
async def report_completion(args: dict[str, Any]) -> dict[str, Any]:
    pct = args["completion_pct"]
    notes = args["notes"]
    from rich.panel import Panel

    pct_color = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    lines = [f"  [{pct_color}]{pct}%[/{pct_color}] complete", f"  {notes}"]
    panel = Panel(
        "\n".join(lines),
        title="[bold]Result[/bold]",
        title_align="left",
        border_style="dim cyan",
        padding=(0, 2),
    )
    from openmax.output import console

    console.print()
    console.print(panel)
    _append_session_event(
        "tool.report_completion",
        {"completion_pct": pct, "notes": notes},
    )
    _record_phase_anchor("report", notes, pct)
    _persist_session_stats()
    return _tool_response(f"Reported {pct}% \u2014 {notes}")
