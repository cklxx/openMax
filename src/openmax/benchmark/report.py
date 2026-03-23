"""Benchmark report generation: terminal table and JSON persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from openmax.benchmark.runner import BenchmarkReport
from openmax.output import console


def print_report(report: BenchmarkReport) -> None:
    """Print a rich comparison table to the terminal."""
    table = Table(title="Benchmark: Claude Code vs openMax")
    table.add_column("Task", style="bold")
    table.add_column("Difficulty", justify="center")
    table.add_column("CC Time", justify="right")
    table.add_column("oM Time", justify="right")
    table.add_column("Speedup", justify="right")
    table.add_column("CC Cost", justify="right")
    table.add_column("oM Cost", justify="right")
    table.add_column("CC", justify="center")
    table.add_column("oM", justify="center")

    for comp in report.comparisons:
        cc = comp.claude_code
        om = comp.openmax
        cc_time = f"{cc.duration_seconds:.1f}s" if cc else "-"
        om_time = f"{om.duration_seconds:.1f}s" if om else "-"
        speedup = f"{comp.speedup:.1f}x" if comp.speedup else "-"
        cc_cost = f"${cc.cost_usd:.4f}" if cc else "-"
        om_cost = f"${om.cost_usd:.4f}" if om else "-"
        cc_pass = _pass_icon(cc)
        om_pass = _pass_icon(om)
        table.add_row(
            comp.task_name,
            comp.difficulty,
            cc_time,
            om_time,
            speedup,
            cc_cost,
            om_cost,
            cc_pass,
            om_pass,
        )

    console.print(table)
    _print_summary(report)


def _pass_icon(result) -> str:
    if not result:
        return "-"
    if result.error:
        return "[red]ERR[/red]"
    return "[green]PASS[/green]" if result.success else "[red]FAIL[/red]"


def _print_summary(report: BenchmarkReport) -> None:
    avg = report.avg_speedup
    speedup_str = f"{avg:.1f}x" if avg else "N/A"

    cc_pass = sum(1 for c in report.comparisons if c.claude_code and c.claude_code.success)
    om_pass = sum(1 for c in report.comparisons if c.openmax and c.openmax.success)
    total = len(report.comparisons)

    cc_cost = sum(c.claude_code.cost_usd for c in report.comparisons if c.claude_code)
    om_cost = sum(c.openmax.cost_usd for c in report.comparisons if c.openmax)
    cost_str = f"{om_cost / cc_cost:.1f}x" if cc_cost > 0 else "N/A"

    console.print(
        f"\nAvg speedup: [bold]{speedup_str}[/bold] | "
        f"Cost ratio: {cost_str} | "
        f"Success: CC {cc_pass}/{total}, oM {om_pass}/{total}"
    )


def save_report(report: BenchmarkReport, output_dir: Path | None = None) -> Path:
    """Save report as JSON for trend tracking."""
    if output_dir is None:
        output_dir = Path(".openmax") / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report.timestamp.replace(':', '-').replace(' ', '_')}.json"
    path = output_dir / filename
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"\nReport saved to [bold]{path}[/bold]")
    return path
