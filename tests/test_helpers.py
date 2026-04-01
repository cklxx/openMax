"""Tests for _helpers — _tool_response truncation, _cap_dict_strings, _extract_smart_output."""

from openmax.lead_agent.tools._helpers import (
    _SMART_OUTPUT_MAX_CHARS,
    _TOOL_RESPONSE_MAX_CHARS,
    _cap_dict_strings,
    _extract_smart_output,
    _tool_response,
)


class TestToolResponse:
    def test_small_data_unchanged(self):
        result = _tool_response({"key": "value"})
        assert '"key"' in result["content"][0]["text"]
        assert "truncated" not in result["content"][0]["text"]

    def test_string_data(self):
        result = _tool_response("hello")
        assert result["content"][0]["text"] == "hello"

    def test_truncates_over_limit(self):
        big = {"data": "x" * (_TOOL_RESPONSE_MAX_CHARS + 100)}
        result = _tool_response(big)
        text = result["content"][0]["text"]
        assert text.endswith("...[truncated]")
        assert len(text) < _TOOL_RESPONSE_MAX_CHARS + 50

    def test_custom_max_chars(self):
        result = _tool_response({"data": "x" * 200}, max_chars=100)
        assert "truncated" in result["content"][0]["text"]

    def test_exact_limit_not_truncated(self):
        data = "x" * 100
        result = _tool_response(data, max_chars=100)
        assert "truncated" not in result["content"][0]["text"]


class TestCapDictStrings:
    def test_short_strings_unchanged(self):
        d = {"a": "short", "b": 42, "c": True}
        assert _cap_dict_strings(d) == d

    def test_long_string_capped(self):
        d = {"msg": "x" * 5000}
        result = _cap_dict_strings(d, max_chars=100)
        assert len(result["msg"]) == 100 + len("...[capped]")
        assert result["msg"].endswith("...[capped]")

    def test_non_string_values_preserved(self):
        d = {"count": 999, "items": [1, 2, 3], "flag": False}
        assert _cap_dict_strings(d) == d

    def test_mixed_dict(self):
        d = {"short": "ok", "long": "y" * 10000, "num": 1}
        result = _cap_dict_strings(d, max_chars=50)
        assert result["short"] == "ok"
        assert result["num"] == 1
        assert result["long"].endswith("...[capped]")

    def test_empty_dict(self):
        assert _cap_dict_strings({}) == {}


class TestExtractSmartOutputCharLimit:
    def test_short_output_unchanged(self):
        text = "\n".join(f"line {i}" for i in range(10))
        assert _extract_smart_output(text) == text

    def test_respects_max_chars(self):
        long_lines = "\n".join("x" * 500 for _ in range(200))
        result = _extract_smart_output(long_lines, max_chars=1000)
        assert len(result) <= 1000

    def test_default_max_chars_value(self):
        assert _SMART_OUTPUT_MAX_CHARS == 30_000

    def test_error_lines_still_surfaced_with_cap(self):
        lines = ["Error: early failure"] + ["padding"] * 200 + ["final line"]
        result = _extract_smart_output("\n".join(lines), tail_lines=50, max_chars=5000)
        assert "[ERROR]" in result

    def test_very_long_single_lines_capped(self):
        text = "x" * 100_000
        result = _extract_smart_output(text, max_chars=500)
        assert len(result) <= 500
