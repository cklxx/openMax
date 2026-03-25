"""AST-based style violation checker for quality workflow."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StyleViolation:
    """A single style violation found by AST analysis."""

    file: str
    function: str
    line: int
    metric: str  # "function_length" | "syntax_error"
    value: int
    threshold: int


def check_style_violations(
    files: list[str],
    max_function_lines: int = 15,
) -> list[StyleViolation]:
    """Check Python files for style violations using AST analysis."""
    violations: list[StyleViolation] = []
    for path in files:
        violations.extend(_check_file(path, max_function_lines))
    return violations


def _check_file(path: str, max_lines: int) -> list[StyleViolation]:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return [StyleViolation(path, "<module>", e.lineno or 1, "syntax_error", 0, 0)]
    return _walk_functions(tree, path, max_lines)


def _walk_functions(tree: ast.Module, path: str, max_lines: int) -> list[StyleViolation]:
    violations: list[StyleViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_lines = _body_line_count(node)
        if body_lines > max_lines:
            violations.append(
                StyleViolation(
                    path,
                    node.name,
                    node.lineno,
                    "function_length",
                    body_lines,
                    max_lines,
                )
            )
    return violations


def _body_line_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if not node.body:
        return 0
    first = node.body[0].lineno
    last = node.body[-1].end_lineno or node.body[-1].lineno
    return last - first + 1


def format_violations(violations: list[StyleViolation]) -> str:
    """Render violations as a fenced code block for safe prompt injection."""
    if not violations:
        return ""
    lines = ["## AST Violations (machine-verified)", "```"]
    for v in violations:
        if v.metric == "syntax_error":
            lines.append(f"  {v.file}:{v.line} — syntax error")
        else:
            lines.append(
                f"  `{v.file}:{v.line}` — `{v.function}` is {v.value} lines (max {v.threshold})"
            )
    lines.append("```")
    return "\n".join(lines)
