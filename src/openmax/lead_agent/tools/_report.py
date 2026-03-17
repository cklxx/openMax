"""Tool for reporting completion."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _record_phase_anchor,
    _remember_run_summary,
    _tool_response,
)


@tool(
    "report_completion",
    "Report overall goal completion percentage and summary. Call exactly once "
    "when all tasks are done. Describe what was delivered, not what was attempted. "
    "This saves a run summary to workspace memory.",
    {"completion_pct": int, "notes": str},
)
async def report_completion(args: dict[str, Any]) -> dict[str, Any]:
    pct = args["completion_pct"]
    notes = args["notes"]
    from rich.panel import Panel

    pct_color = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    panel = Panel(
        f"  [{pct_color}]{pct}%[/{pct_color}] complete\n  {notes}",
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
    _remember_run_summary(notes, pct)
    return _tool_response(f"Reported {pct}% \u2014 {notes}")
