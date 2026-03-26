"""Tests for stream-json event parser."""

from __future__ import annotations

import json

from openmax.stream_parser import parse_stream_line


def test_parse_init_event():
    line = json.dumps({"type": "system", "subtype": "init", "tools": ["Read", "Write"]})
    event = parse_stream_line(line)
    assert event is not None
    assert event.type == "init"
    assert "initializing" in event.summary


def test_parse_tool_use_read():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "src/foo.py"}}
                ]
            },
        }
    )
    event = parse_stream_line(line)
    assert event is not None
    assert event.type == "tool_use"
    assert "Read" in event.summary
    assert "src/foo.py" in event.summary


def test_parse_tool_use_bash():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ruff check ."}}
                ]
            },
        }
    )
    event = parse_stream_line(line)
    assert event.type == "tool_use"
    assert "ruff check" in event.summary


def test_parse_text_block():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Here is the result of the analysis."}]
            },
        }
    )
    event = parse_stream_line(line)
    assert event is not None
    assert event.type == "text"
    assert "result" in event.summary


def test_parse_result_event():
    line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.0523,
            "num_turns": 3,
            "duration_ms": 15000,
            "result": "Done.",
        }
    )
    event = parse_stream_line(line)
    assert event is not None
    assert event.type == "result"
    assert "$0.0523" in event.summary
    assert "15.0s" in event.summary


def test_parse_empty_line():
    assert parse_stream_line("") is None
    assert parse_stream_line("   ") is None


def test_parse_malformed_json():
    assert parse_stream_line("not json at all") is None
    assert parse_stream_line("{broken") is None


def test_parse_unknown_type():
    line = json.dumps({"type": "rate_limit_event", "info": {}})
    assert parse_stream_line(line) is None


def test_parse_assistant_no_content():
    line = json.dumps({"type": "assistant", "message": {"content": []}})
    assert parse_stream_line(line) is None


def test_summary_truncation():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "x" * 200}]},
        }
    )
    event = parse_stream_line(line)
    assert event is not None
    assert len(event.summary) <= 80
