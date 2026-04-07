"""Error context extraction from agent output."""

from __future__ import annotations

import re

_ANSI_ESC_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07")
_ERROR_MARKERS = ("Error", "FAILED", "Traceback", "panic:", "FATAL", "Exception", "[ERROR]")
_CONTEXT_BEFORE = 5

_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    "overloaded",
    "resource_exhausted",
)


def is_rate_limit_error(text: str) -> bool:
    """Detect rate-limit / overloaded signals in agent output."""
    lower = _strip_ansi(text).lower()
    return any(p in lower for p in _RATE_LIMIT_PATTERNS)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESC_RE.sub("", text)


def _find_error_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Find (start, end) ranges for each error block in lines."""
    blocks: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if not any(m in line for m in _ERROR_MARKERS):
            continue
        start = max(0, i - _CONTEXT_BEFORE)
        end = i + 1
        while end < len(lines) and lines[end].strip():
            end += 1
        blocks.append((start, end))
    return _merge_overlapping(blocks)


def _merge_overlapping(blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not blocks:
        return []
    merged = [blocks[0]]
    for start, end in blocks[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def extract_error_context(output: str, max_chars: int = 8000) -> str:
    """Extract syntax-aware error context from agent output.

    Scans for error markers, extracts surrounding context, strips ANSI.
    Falls back to last 20 lines when no markers are found.
    """
    if not output:
        return ""
    cleaned = _strip_ansi(output)
    lines = cleaned.splitlines()
    blocks = _find_error_blocks(lines)
    if not blocks:
        return "\n".join(lines[-20:])[:max_chars]
    parts: list[str] = []
    total = 0
    for start, end in blocks:
        chunk = "\n".join(lines[start:end])
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                parts.append(chunk[:remaining])
            break
        parts.append(chunk)
        total += len(chunk) + 4  # account for separator
    return "\n---\n".join(parts)[:max_chars]
