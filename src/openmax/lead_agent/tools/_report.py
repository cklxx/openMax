"""Tool for reporting completion."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _record_phase_anchor,
    _tool_response,
)


def _get_scorecard():
    """Best-effort fetch of the current run scorecard, or None."""
    try:
        from openmax.lead_agent.runtime import get_lead_agent_runtime

        rt = get_lead_agent_runtime()
        if rt.session_store and rt.session_meta:
            snap = rt.session_store.load_snapshot(rt.session_meta.session_id)
            return snap.plan.scorecard
    except Exception:
        pass
    return None


@tool(
    "report_completion",
    "Report overall goal completion percentage and summary. Call exactly once "
    "when all tasks are done. Describe what was delivered, not what was attempted. "
    "This records a phase anchor for session tracking.",
    {"completion_pct": int, "notes": str},
)
async def report_completion(args: dict[str, Any]) -> dict[str, Any]:
    pct = args["completion_pct"]
    notes = args["notes"]
    from rich.panel import Panel

    pct_color = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    lines = [f"  [{pct_color}]{pct}%[/{pct_color}] complete", f"  {notes}"]
    scorecard = _get_scorecard()
    if scorecard and scorecard.acceleration_ratio is not None:
        lines.append(f"  [dim]{scorecard.surface_acceleration}[/dim]")
    if scorecard and scorecard.overhead is not None:
        lines.append(f"  [dim]{scorecard.overhead.surface()}[/dim]")
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
    return _tool_response(f"Reported {pct}% \u2014 {notes}")
