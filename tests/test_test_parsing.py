"""Tests for the test output parser module."""

from __future__ import annotations

from openmax.test_parsing import ParsedTestResult, detect_framework, parse_test_output

# ---------------------------------------------------------------------------
# detect_framework
# ---------------------------------------------------------------------------


class TestDetectFramework:
    def test_pytest_summary_line(self):
        raw = "===== 3 passed, 1 failed in 2.31s ====="
        assert detect_framework(raw) == "pytest"

    def test_jest_summary_line(self):
        raw = "Tests:  1 failed, 12 passed, 13 total"
        assert detect_framework(raw) == "jest"

    def test_go_test_ok(self):
        raw = "ok  \tgithub.com/foo/bar\t0.042s"
        assert detect_framework(raw) == "go_test"

    def test_go_test_fail(self):
        raw = "FAIL\tgithub.com/foo/bar\t0.042s"
        assert detect_framework(raw) == "go_test"

    def test_cargo_test(self):
        raw = "test result: ok. 5 passed; 0 failed; 1 ignored; 0 measured"
        assert detect_framework(raw) == "cargo_test"

    def test_unknown(self):
        assert detect_framework("hello world") is None

    def test_ansi_stripped(self):
        raw = "\x1b[32m===== 3 passed in 0.5s =====\x1b[0m"
        assert detect_framework(raw) == "pytest"


# ---------------------------------------------------------------------------
# parse_test_output — empty / unknown
# ---------------------------------------------------------------------------


class TestParseEmpty:
    def test_empty_string(self):
        r = parse_test_output("")
        assert r == ParsedTestResult()

    def test_whitespace_only(self):
        r = parse_test_output("   \n\n  ")
        assert r == ParsedTestResult()


class TestParseUnknown:
    def test_generic_counts(self):
        raw = "line1 PASS\nline2 FAIL\nline3 PASS\nline4 ERROR\n"
        r = parse_test_output(raw)
        assert r.passed == 2
        assert r.failed == 1
        assert r.errors == 1

    def test_failure_summaries_capped(self):
        raw = "\n".join(f"FAIL test_{i}" for i in range(10))
        r = parse_test_output(raw)
        assert len(r.failure_summaries) <= 5


# ---------------------------------------------------------------------------
# parse_test_output — pytest
# ---------------------------------------------------------------------------


class TestParsePytest:
    def test_all_counts(self):
        raw = "===== 10 passed, 2 failed, 1 error, 3 skipped in 4.2s ====="
        r = parse_test_output(raw)
        assert r.framework == "pytest"
        assert r.passed == 10
        assert r.failed == 2
        assert r.errors == 1
        assert r.skipped == 3

    def test_only_passed(self):
        raw = "===== 5 passed in 1.0s ====="
        r = parse_test_output(raw)
        assert r.passed == 5
        assert r.failed == 0

    def test_with_ansi(self):
        raw = "\x1b[1m\x1b[32m===== 3 passed in 0.5s =====\x1b[0m"
        r = parse_test_output(raw)
        assert r.passed == 3

    def test_failure_lines_extracted(self):
        raw = (
            "FAILED tests/test_foo.py::test_bar - AssertionError\n"
            "FAILED tests/test_foo.py::test_baz - ValueError\n"
            "===== 1 passed, 2 failed in 1.0s ====="
        )
        r = parse_test_output(raw)
        assert r.failed == 2
        assert len(r.failure_summaries) >= 2

    def test_forced_framework(self):
        raw = "===== 3 passed in 0.5s ====="
        r = parse_test_output(raw, framework="pytest")
        assert r.framework == "pytest"

    def test_raw_tail(self):
        lines = [f"line {i}" for i in range(30)]
        raw = "\n".join(lines) + "\n===== 1 passed in 0.1s ====="
        r = parse_test_output(raw, framework="pytest")
        tail_lines = r.raw_tail.strip().splitlines()
        assert len(tail_lines) <= 20


# ---------------------------------------------------------------------------
# parse_test_output — jest
# ---------------------------------------------------------------------------


class TestParseJest:
    def test_basic(self):
        raw = (
            "PASS src/foo.test.js\n"
            "FAIL src/bar.test.js\n"
            "  ● should do thing\n"
            "Tests:  1 failed, 4 passed, 5 total\n"
        )
        r = parse_test_output(raw)
        assert r.framework == "jest"
        assert r.passed == 4
        assert r.failed == 1

    def test_all_passed(self):
        raw = "Tests:  10 passed, 10 total"
        r = parse_test_output(raw)
        assert r.framework == "jest"
        assert r.passed == 10
        assert r.failed == 0


# ---------------------------------------------------------------------------
# parse_test_output — go test
# ---------------------------------------------------------------------------


class TestParseGoTest:
    def test_mixed(self):
        raw = "--- FAIL: TestFoo (0.01s)\nok  \tgithub.com/x/a\t0.1s\nFAIL\tgithub.com/x/b\t0.2s\n"
        r = parse_test_output(raw)
        assert r.framework == "go_test"
        assert r.passed == 1
        assert r.failed == 1
        assert len(r.failure_summaries) >= 1

    def test_all_ok(self):
        raw = "ok  \tgithub.com/x/a\t0.1s\nok  \tgithub.com/x/b\t0.2s\n"
        r = parse_test_output(raw)
        assert r.passed == 2
        assert r.failed == 0


# ---------------------------------------------------------------------------
# parse_test_output — cargo test
# ---------------------------------------------------------------------------


class TestParseCargoTest:
    def test_basic(self):
        raw = (
            "running 6 tests\n"
            "test tests::test_a ... ok\n"
            "test tests::test_b ... FAILED\n"
            "---- tests::test_b stdout ----\n"
            "assertion failed\n"
            "test result: FAILED. 5 passed; 1 failed; 0 ignored; 0 measured\n"
        )
        r = parse_test_output(raw)
        assert r.framework == "cargo_test"
        assert r.passed == 5
        assert r.failed == 1
        assert any("test_b" in s for s in r.failure_summaries)

    def test_all_pass(self):
        raw = "test result: ok. 10 passed; 0 failed; 2 ignored; 0 measured"
        r = parse_test_output(raw)
        assert r.passed == 10
        assert r.skipped == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_truncated_output_no_summary(self):
        raw = "running tests...\ntest_a PASS\ntest_b PASS\n"
        r = parse_test_output(raw, framework="pytest")
        assert r.framework == "pytest"
        assert r.raw_tail != ""

    def test_summary_truncation(self):
        long_line = "FAIL " + "x" * 300
        raw = f"{long_line}\n===== 0 passed, 1 failed in 0.1s ====="
        r = parse_test_output(raw, framework="pytest")
        for s in r.failure_summaries:
            assert len(s) <= 201  # 200 + ellipsis char

    def test_mixed_stderr_stdout(self):
        raw = (
            "collecting...\n"
            "ERROR during collection\n"
            "===== 0 passed, 0 failed, 1 error in 0.5s ====="
        )
        r = parse_test_output(raw, framework="pytest")
        assert r.errors == 1
