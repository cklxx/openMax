"""Pure-function DAG renderer for task dependency visualization."""

from __future__ import annotations

from collections import deque

STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    "running": ("bold yellow", "●", "⟳"),
    "done": ("green", "✓", ""),
    "error": ("red", "✗", ""),
    "pending": ("dim", "○", ""),
}

STATUS_SYMBOLS: dict[str, str] = {k: v[1] for k, v in STATUS_STYLE.items()}

_COMPACT_THRESHOLD = 20


def _style_node(name: str, status: str, front: set[str]) -> str:
    style, icon, anim = STATUS_STYLE.get(status, ("dim", "?", ""))
    indicator = f" {anim}" if anim else ""
    highlight = " ◀" if name in front else ""
    return f"[{style}][{icon}] {name}{indicator}{highlight}[/{style}]"


def _detect_cycle(deps: dict[str, list[str]], all_tasks: set[str]) -> bool:
    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        for dep in deps.get(node, []):
            if dep not in all_tasks:
                continue
            if dep in in_stack:
                return True
            if dep not in visited and dfs(dep):
                return True
        in_stack.discard(node)
        return False

    return any(dfs(t) for t in all_tasks if t not in visited)


def _topo_layers(
    deps: dict[str, list[str]],
    all_tasks: set[str],
) -> list[list[str]]:
    in_degree: dict[str, int] = {t: 0 for t in all_tasks}
    children: dict[str, list[str]] = {t: [] for t in all_tasks}
    for task, parents in deps.items():
        if task not in all_tasks:
            continue
        for p in parents:
            if p in all_tasks:
                in_degree[task] += 1
                children[p].append(task)

    queue: deque[str] = deque(t for t, d in in_degree.items() if d == 0)
    layers: list[list[str]] = []
    while queue:
        layer = sorted(queue)
        layers.append(layer)
        next_q: deque[str] = deque()
        for node in layer:
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_q.append(child)
        queue = next_q
    orphans = sorted(all_tasks - {t for layer in layers for t in layer})
    if orphans:
        layers.append(orphans)
    return layers


def _execution_front(
    statuses: dict[str, str],
    deps: dict[str, list[str]],
) -> set[str]:
    running = {t for t, s in statuses.items() if s == "running"}
    front = set(running)
    for t in running:
        front.update(deps.get(t, []))
    return front


def _render_compact(
    all_tasks: set[str],
    statuses: dict[str, str],
    deps: dict[str, list[str]],
    front: set[str],
) -> str:
    lines: list[str] = ["[bold]DAG (compact)[/bold]", ""]
    for name in sorted(all_tasks):
        parents = [p for p in deps.get(name, []) if p in all_tasks]
        suffix = f" ← {', '.join(parents)}" if parents else ""
        lines.append(f"  {_style_node(name, statuses.get(name, 'pending'), front)}{suffix}")
    return "\n".join(lines)


def _render_vertical_list(
    all_tasks: set[str],
    statuses: dict[str, str],
    front: set[str],
) -> str:
    lines: list[str] = ["[bold]DAG (narrow)[/bold]", ""]
    for name in sorted(all_tasks):
        lines.append(f"  {_style_node(name, statuses.get(name, 'pending'), front)}")
    return "\n".join(lines)


def _render_layers(
    layers: list[list[str]],
    statuses: dict[str, str],
    front: set[str],
    width: int,
) -> str:
    lines: list[str] = []
    for i, layer in enumerate(layers):
        nodes = [_style_node(n, statuses.get(n, "pending"), front) for n in layer]
        row = "  ".join(nodes)
        lines.append(row)
        if i < len(layers) - 1:
            arrow = "│" if len(layer) == 1 else "┬"
            lines.append(f"{'':>{width // 2}}{arrow}")
            if len(layers[i + 1]) > 1 and len(layer) == 1:
                lines.append(f"{'':>{width // 2}}┼{'─►' * (len(layers[i + 1]) - 1)}")
            elif len(layers[i + 1]) == 1 and len(layer) > 1:
                lines.append(f"{'':>{width // 2}}┴")
    return "\n".join(lines)


def render_dag(
    parallel_groups: list[list[str]],
    statuses: dict[str, str],
    deps: dict[str, list[str]] | None = None,
    terminal_width: int = 120,
) -> str:
    """Render task DAG with status styling and dependency layout.

    Args:
        parallel_groups: Fallback flat list of task groups (used if no deps).
        statuses: Task name -> status string.
        deps: Task name -> list of dependency task names.
        terminal_width: Available width for rendering.

    Returns:
        Rich-markup string for the DAG visualization.
    """
    all_tasks = {t for g in parallel_groups for t in g}
    all_tasks.update(statuses.keys())
    if deps:
        all_tasks.update(deps.keys())
        for parents in deps.values():
            all_tasks.update(parents)

    if not all_tasks:
        return "[dim](no tasks)[/dim]"

    effective_deps = deps or {}
    front = _execution_front(statuses, effective_deps)

    has_cycle = _detect_cycle(effective_deps, all_tasks) if effective_deps else False
    warning = "[bold red]⚠ Cycle detected — showing flat view[/bold red]\n\n" if has_cycle else ""

    if has_cycle or not effective_deps:
        return warning + _render_compact(all_tasks, statuses, effective_deps, front)

    if len(all_tasks) > _COMPACT_THRESHOLD:
        return _render_compact(all_tasks, statuses, effective_deps, front)

    if terminal_width < 60:
        return _render_vertical_list(all_tasks, statuses, front)

    layers = _topo_layers(effective_deps, all_tasks)
    return _render_layers(layers, statuses, front, terminal_width)
