"""Tests for dispatch module — extract_error_context, smart retry, and adaptive stuck threshold."""

from openmax.lead_agent.tools._dispatch import (
    _RETRY_CONTEXT_MAX_CHARS,
    _build_retry_prompt,
    extract_error_context,
    get_stuck_threshold,
)
from openmax.stats import SessionStats


class TestExtractErrorContext:
    def test_empty_output(self):
        assert extract_error_context("") == ""

    def test_no_markers_falls_back_to_last_20_lines(self):
        lines = [f"line {i}" for i in range(50)]
        result = extract_error_context("\n".join(lines))
        assert result == "\n".join(lines[-20:])

    def test_no_markers_short_output(self):
        result = extract_error_context("just a few lines\nnothing special")
        assert result == "just a few lines\nnothing special"

    def test_single_error_marker(self):
        lines = [
            "setup step 1",
            "setup step 2",
            "setup step 3",
            "setup step 4",
            "setup step 5",
            "setup step 6",
            "setup step 7",
            "Error: something broke",
            "details about the error",
            "more details",
            "",
            "unrelated stuff after",
        ]
        result = extract_error_context("\n".join(lines))
        assert "Error: something broke" in result
        assert "details about the error" in result
        assert "more details" in result
        # 5 lines of context before the error marker
        assert "setup step 3" in result
        # blank line terminates the block
        assert "unrelated stuff after" not in result

    def test_traceback_extraction(self):
        lines = [
            "Running tests...",
            "collected 5 items",
            "",
            "Traceback (most recent call last):",
            '  File "test.py", line 10, in test_foo',
            "    result = foo()",
            '  File "foo.py", line 5, in foo',
            "    raise ValueError('bad')",
            "ValueError: bad",
            "",
            "other output",
        ]
        result = extract_error_context("\n".join(lines))
        assert "Traceback (most recent call last):" in result
        assert "ValueError: bad" in result

    def test_multiple_errors_concatenated(self):
        lines = [
            "first block",
            "",
            "Error: first problem",
            "detail 1",
            "",
            "some good output",
            "more good output",
            "",
            "FAILED test_something",
            "assertion detail",
            "",
            "end",
        ]
        result = extract_error_context("\n".join(lines))
        assert "Error: first problem" in result
        assert "FAILED test_something" in result

    def test_ansi_stripped(self):
        text = "\x1b[31mError: red error\x1b[0m\ndetails\n"
        result = extract_error_context(text)
        assert "\x1b[" not in result
        assert "Error: red error" in result

    def test_max_chars_truncation(self):
        lines = ["Error: " + "x" * 100 for _ in range(50)]
        result = extract_error_context("\n".join(lines), max_chars=200)
        assert len(result) <= 200

    def test_panic_marker(self):
        lines = ["init done", "loading config", "panic: runtime error", "goroutine 1"]
        result = extract_error_context("\n".join(lines))
        assert "panic: runtime error" in result
        assert "goroutine 1" in result

    def test_fatal_marker(self):
        lines = ["step 1", "step 2", "FATAL: out of memory", ""]
        result = extract_error_context("\n".join(lines))
        assert "FATAL: out of memory" in result

    def test_error_bracket_marker(self):
        lines = ["[INFO] starting", "[ERROR] connection refused", "retrying...", ""]
        result = extract_error_context("\n".join(lines))
        assert "[ERROR] connection refused" in result

    def test_exception_marker(self):
        lines = ["processing", "Exception in thread main", "stack info", ""]
        result = extract_error_context("\n".join(lines))
        assert "Exception in thread main" in result

    def test_context_before_clamped_to_start(self):
        lines = ["Error: at the very start", "detail", ""]
        result = extract_error_context("\n".join(lines))
        assert "Error: at the very start" in result

    def test_error_at_end_no_blank_line(self):
        lines = ["setup", "more setup", "Error: final line"]
        result = extract_error_context("\n".join(lines))
        assert "Error: final line" in result

    def test_binary_garbage_no_crash(self):
        garbage = b"\x00\xff\xfe\x80\x81".decode("latin-1")
        result = extract_error_context(garbage)
        assert isinstance(result, str)

    def test_overlapping_blocks_merged(self):
        lines = [
            "Error: first",
            "Error: second right after",
            "detail",
            "",
        ]
        result = extract_error_context("\n".join(lines))
        assert "Error: first" in result
        assert "Error: second right after" in result
        # Should be one block, not separated by ---
        assert "---" not in result

    def test_fallback_respects_max_chars(self):
        lines = ["x" * 200 for _ in range(30)]
        result = extract_error_context("\n".join(lines), max_chars=500)
        assert len(result) <= 500


class TestBuildRetryPrompt:
    def test_retry_with_error_context(self):
        result = _build_retry_prompt("do the task", "Error: something broke")
        assert "[RETRY CONTEXT]" in result
        assert "Error: something broke" in result
        assert "do the task" in result
        assert result.endswith("do the task")

    def test_original_prompt_preserved(self):
        original = "implement feature X with files a.py, b.py"
        result = _build_retry_prompt(original, "Error: test failed")
        assert original in result
        assert result.index("[RETRY CONTEXT]") < result.index(original)

    def test_empty_error_context_returns_original(self):
        original = "do the task"
        result = _build_retry_prompt(original, "")
        assert result == original

    def test_error_context_truncation_via_extract(self):
        long_output = "Error: " + "x" * 5000
        ctx = extract_error_context(long_output, max_chars=_RETRY_CONTEXT_MAX_CHARS)
        assert len(ctx) <= _RETRY_CONTEXT_MAX_CHARS
        result = _build_retry_prompt("do task", ctx)
        assert "do task" in result

    def test_retry_prompt_structure(self):
        result = _build_retry_prompt("original", "err details")
        lines = result.split("\n")
        assert lines[0] == "[RETRY CONTEXT] Previous attempt failed. Error summary:"
        assert "err details" in result
        assert "different approach" in result
        assert "---" in result


class TestGetStuckThreshold:
    def test_no_stats_returns_base(self):
        assert get_stuck_threshold(None) == 3

    def test_zero_false_positive_rate_returns_base(self):
        stats = SessionStats(stuck_false_positive_rate=0.0)
        assert get_stuck_threshold(stats) == 3

    def test_low_false_positive_rate_returns_base(self):
        stats = SessionStats(stuck_false_positive_rate=0.2)
        assert get_stuck_threshold(stats) == 3

    def test_medium_false_positive_rate_returns_5(self):
        stats = SessionStats(stuck_false_positive_rate=0.4)
        assert get_stuck_threshold(stats) == 5

    def test_high_false_positive_rate_returns_7(self):
        stats = SessionStats(stuck_false_positive_rate=0.6)
        assert get_stuck_threshold(stats) == 7

    def test_boundary_at_0_3_returns_base(self):
        stats = SessionStats(stuck_false_positive_rate=0.3)
        assert get_stuck_threshold(stats) == 3

    def test_boundary_at_0_5_returns_5(self):
        stats = SessionStats(stuck_false_positive_rate=0.5)
        assert get_stuck_threshold(stats) == 5

    def test_result_within_clamp_range(self):
        for rate in [0.0, 0.1, 0.3, 0.5, 0.7, 1.0]:
            stats = SessionStats(stuck_false_positive_rate=rate)
            result = get_stuck_threshold(stats)
            assert 2 <= result <= 10
