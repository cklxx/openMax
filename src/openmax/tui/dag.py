"""Pure-function DAG renderer for task dependency visualization."""

STATUS_SYMBOLS: dict[str, str] = {
    "pending": "○",
    "running": "●",
    "done": "✓",
    "error": "✗",
}


def _status_symbol(status: str) -> str:
    return STATUS_SYMBOLS.get(status, "?")


def _render_node(name: str, status: str) -> str:
    return f"[{name}]{_status_symbol(status)}"


def _center_pad(text: str, width: int) -> str:
    pad = max(0, width - len(text))
    return " " * (pad // 2) + text


def _group_line(
    names: list[str],
    statuses: dict[str, str],
    total_width: int,
) -> tuple[str, list[int]]:
    """Render a group of nodes and return (line, center_positions)."""
    nodes = [_render_node(n, statuses.get(n, "pending")) for n in names]
    content = "  ".join(nodes)
    offset = max(0, (total_width - len(content)) // 2)
    positions: list[int] = []
    pos = offset
    for node in nodes:
        positions.append(pos + len(node) // 2)
        pos += len(node) + 2
    return " " * offset + content, positions


def _total_width(groups: list[list[str]], statuses: dict[str, str]) -> int:
    widths = []
    for g in groups:
        nodes = [_render_node(n, statuses.get(n, "pending")) for n in g]
        widths.append(len("  ".join(nodes)))
    return max(widths) if widths else 0


def render_dag(
    parallel_groups: list[list[str]],
    statuses: dict[str, str],
) -> str:
    """Render parallel_groups as ASCII DAG with status symbols.

    Args:
        parallel_groups: Each inner list is a group of concurrent tasks.
                         Groups are sequential (group N+1 depends on group N).
        statuses: Task name -> status string (pending/running/done/error).

    Returns:
        Multi-line ASCII string with Unicode box-drawing characters.
    """
    if not parallel_groups:
        return ""
    groups = [g for g in parallel_groups if g]
    if not groups:
        return ""
    width = _total_width(groups, statuses)
    lines: list[str] = []
    prev_count = 0
    for i, group in enumerate(groups):
        if i > 0:
            _add_transition(lines, prev_count, len(group), width)
        line, _ = _group_line(group, statuses, width)
        lines.append(line)
        prev_count = len(group)
    return "\n".join(line.rstrip() for line in lines)


def _add_transition(
    lines: list[str],
    prev_count: int,
    curr_count: int,
    width: int,
) -> None:
    """Add connector lines between two groups."""
    if prev_count == 1 and curr_count == 1:
        lines.append(_center_pad("│", width))
    elif prev_count == 1 and curr_count > 1:
        lines.append(_center_pad("│", width))
        _add_fork(lines, width)
    elif prev_count > 1 and curr_count == 1:
        _add_merge(lines, width)
        lines.append(_center_pad("│", width))
    else:
        _add_merge(lines, width)
        lines.append(_center_pad("│", width))
        _add_fork(lines, width)


def _add_fork(lines: list[str], width: int) -> None:
    mid = width // 2
    spread = width // 3
    left, right = mid - spread, mid + spread
    bar = [" "] * width
    for i in range(left, right + 1):
        bar[i] = "─"
    bar[left] = "┌"
    bar[right] = "┐"
    bar[mid] = "┴"
    lines.append("".join(bar))


def _add_merge(lines: list[str], width: int) -> None:
    mid = width // 2
    spread = width // 3
    left, right = mid - spread, mid + spread
    bar = [" "] * width
    for i in range(left, right + 1):
        bar[i] = "─"
    bar[left] = "└"
    bar[right] = "┘"
    bar[mid] = "┬"
    lines.append("".join(bar))
