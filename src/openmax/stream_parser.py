"""Parse stream-json events from ``claude -p --output-format stream-json --verbose``."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """One parsed event from a stream-json line."""

    type: str  # init | tool_use | text | result | error
    summary: str  # human-readable one-liner for dashboard
    raw: dict[str, Any]


StreamCallback = Callable[[int, StreamEvent], None]

_MAX_SUMMARY = 80


def parse_stream_line(line: str) -> StreamEvent | None:
    """Parse a single JSON line into a StreamEvent, or None on failure."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("Malformed stream JSON line: %.100s", line)
        return None
    event_type = data.get("type", "")
    if event_type == "system":
        return StreamEvent("init", "initializing…", data)
    if event_type == "assistant":
        return _parse_assistant_event(data)
    if event_type == "result":
        return _parse_result_event(data)
    return None


def _parse_assistant_event(data: dict[str, Any]) -> StreamEvent | None:
    """Extract tool_use or text from an assistant message event."""
    msg = data.get("message", {})
    blocks = msg.get("content") or []
    for block in blocks:
        if block.get("type") == "tool_use":
            return _parse_tool_use(block, data)
        if block.get("type") == "text":
            return _parse_text_block(block, data)
    return None


def _parse_tool_use(block: dict[str, Any], raw: dict[str, Any]) -> StreamEvent:
    name = block.get("name", "?")
    inp = block.get("input", {})
    detail = _tool_detail(name, inp)
    summary = f"{name} {detail}" if detail else name
    return StreamEvent("tool_use", summary[:_MAX_SUMMARY], raw)


def _tool_detail(name: str, inp: dict[str, Any]) -> str:
    if name in ("Read", "Glob", "Grep"):
        return inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
    if name in ("Edit", "Write"):
        return inp.get("file_path", "")
    if name == "Bash":
        cmd = inp.get("command", "")
        return cmd[:60] if cmd else ""
    return ""


def _parse_text_block(block: dict[str, Any], raw: dict[str, Any]) -> StreamEvent:
    text = block.get("text", "").strip()
    first_line = text.split("\n", 1)[0]
    return StreamEvent("text", first_line[:_MAX_SUMMARY], raw)


def _parse_result_event(data: dict[str, Any]) -> StreamEvent:
    cost = data.get("total_cost_usd", 0)
    turns = data.get("num_turns", 0)
    duration = data.get("duration_ms", 0)
    summary = f"done · {turns} turns · {duration / 1000:.1f}s · ${cost:.4f}"
    return StreamEvent("result", summary, data)
