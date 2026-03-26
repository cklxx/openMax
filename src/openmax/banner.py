"""Startup banner for openMax CLI."""

from __future__ import annotations

from rich.text import Text

from openmax import __version__
from openmax.output import console
from openmax.theme import get_theme


def render_banner(
    *,
    session_id: str | None = None,
    resume: bool = False,
    task_count: int | None = None,
) -> list[Text]:
    """Build the startup banner as a list of Rich Text lines."""
    t = get_theme()
    line1 = Text()
    line1.append("  \u26a1 ", style=t.banner_icon)
    line1.append("openMax", style=t.banner_brand)
    line1.append(f"  v{__version__}", style=t.banner_version)

    meta: list[str] = []
    if task_count:
        meta.append(f"{task_count} tasks")
    if session_id:
        meta.append(f"session {session_id}")
    if resume:
        meta.append("resume")
    if meta:
        line1.append("  " + " \u00b7 ".join(meta), style=t.banner_meta)

    line2 = Text("     multi-agent orchestration", style=t.banner_tagline)
    return [line1, line2]


def print_banner(
    *,
    session_id: str | None = None,
    resume: bool = False,
    task_count: int | None = None,
) -> None:
    """Print the startup banner to the console."""
    console.print()
    for line in render_banner(session_id=session_id, resume=resume, task_count=task_count):
        console.print(line)
    console.print()
