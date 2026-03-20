"""Parse structured results from test runner output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_MAX_SUMMARIES = 5
_MAX_SUMMARY_LEN = 200
_RAW_TAIL_LINES = 20


@dataclass
class TestResult:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    failure_summaries: list[str] = field(default_factory=list)
    is_flaky: bool = False
    framework: str | None = None
    raw_tail: str = ""


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _tail(text: str, n: int = _RAW_TAIL_LINES) -> str:
    lines = text.rstrip("\n").splitlines()
    return "\n".join(lines[-n:])


def _clip(s: str, limit: int = _MAX_SUMMARY_LEN) -> str:
    return s[:limit] + "…" if len(s) > limit else s


def _collect_summaries(lines: list[str], limit: int = _MAX_SUMMARIES) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            out.append(_clip(stripped))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

_PYTEST_SUMMARY = re.compile(r"=+\s.*(passed|failed|error|skipped).*=+")
_JEST_SUMMARY = re.compile(r"Tests:\s+.*(?:passed|failed)", re.IGNORECASE)
_GO_RESULT = re.compile(r"^(ok|FAIL)\s+\S+", re.MULTILINE)
_CARGO_RESULT = re.compile(r"test result:.*\d+ passed")


def detect_framework(raw: str) -> str | None:
    clean = _strip_ansi(raw)
    if _PYTEST_SUMMARY.search(clean) or "pytest" in clean.lower():
        return "pytest"
    if _JEST_SUMMARY.search(clean) or "jest" in clean.lower():
        return "jest"
    if _CARGO_RESULT.search(clean):
        return "cargo_test"
    if _GO_RESULT.search(clean):
        return "go_test"
    return None


# ---------------------------------------------------------------------------
# Per-framework parsers
# ---------------------------------------------------------------------------

_PYTEST_COUNTS = re.compile(
    r"(?:(\d+) passed)?"
    r"(?:,?\s*(\d+) failed)?"
    r"(?:,?\s*(\d+) error)?"
    r"(?:,?\s*(\d+) skipped)?"
)


def _parse_pytest(clean: str) -> TestResult:
    passed = failed = errors = skipped = 0
    for m in _PYTEST_COUNTS.finditer(clean):
        if m.group(1):
            passed = max(passed, int(m.group(1)))
        if m.group(2):
            failed = max(failed, int(m.group(2)))
        if m.group(3):
            errors = max(errors, int(m.group(3)))
        if m.group(4):
            skipped = max(skipped, int(m.group(4)))
    summaries = _extract_pytest_failures(clean)
    return TestResult(
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        failure_summaries=summaries,
        framework="pytest",
    )


def _extract_pytest_failures(clean: str) -> list[str]:
    blocks: list[str] = []
    lines = clean.splitlines()
    capture = False
    buf: list[str] = []
    for line in lines:
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            blocks.append(_clip(line.strip()))
        if re.match(r"_{3,}\s+\S+", line):
            if buf:
                blocks.append(_clip(" ".join(buf)))
                buf = []
            capture = True
            continue
        if capture:
            if re.match(r"[=_]{3,}", line):
                capture = False
                if buf:
                    blocks.append(_clip(" ".join(buf)))
                    buf = []
            else:
                buf.append(line.strip())
    if buf:
        blocks.append(_clip(" ".join(buf)))
    return blocks[:_MAX_SUMMARIES]


_JEST_COUNTS = re.compile(
    r"Tests:\s+"
    r"(?:(\d+)\s+failed,?\s*)?"
    r"(?:(\d+)\s+skipped,?\s*)?"
    r"(?:(\d+)\s+passed)?"
)


def _parse_jest(clean: str) -> TestResult:
    m = _JEST_COUNTS.search(clean)
    failed = int(m.group(1)) if m and m.group(1) else 0
    skipped = int(m.group(2)) if m and m.group(2) else 0
    passed = int(m.group(3)) if m and m.group(3) else 0
    fail_lines = [
        _clip(line.strip()) for line in clean.splitlines() if "FAIL " in line or "● " in line
    ]
    return TestResult(
        passed=passed,
        failed=failed,
        skipped=skipped,
        failure_summaries=fail_lines[:_MAX_SUMMARIES],
        framework="jest",
    )


def _parse_go_test(clean: str) -> TestResult:
    passed = len(re.findall(r"^ok\s+\S+", clean, re.MULTILINE))
    failed = len(re.findall(r"^FAIL\s+\S+", clean, re.MULTILINE))
    fail_names = re.findall(r"--- FAIL:\s+(.+)", clean)
    summaries = [_clip(f"FAIL: {n.strip()}") for n in fail_names]
    return TestResult(
        passed=passed,
        failed=failed,
        failure_summaries=summaries[:_MAX_SUMMARIES],
        framework="go_test",
    )


_CARGO_COUNTS = re.compile(r"test result:.*?(\d+) passed;\s*(\d+) failed;\s*(\d+) ignored")


def _parse_cargo(clean: str) -> TestResult:
    m = _CARGO_COUNTS.search(clean)
    passed = int(m.group(1)) if m else 0
    failed = int(m.group(2)) if m else 0
    skipped = int(m.group(3)) if m else 0
    fail_names = re.findall(r"---- (\S+) stdout ----", clean)
    summaries = [_clip(f"FAIL: {n}") for n in fail_names]
    return TestResult(
        passed=passed,
        failed=failed,
        skipped=skipped,
        failure_summaries=summaries[:_MAX_SUMMARIES],
        framework="cargo_test",
    )


def _parse_generic(clean: str) -> TestResult:
    lines = clean.splitlines()
    passed = sum(1 for ln in lines if re.search(r"\bPASS\b", ln))
    failed = sum(1 for ln in lines if re.search(r"\bFAIL\b", ln))
    errors = sum(1 for ln in lines if re.search(r"\bERROR\b", ln))
    fail_lines = [_clip(ln.strip()) for ln in lines if re.search(r"\bFAIL\b|\bERROR\b", ln)]
    return TestResult(
        passed=passed,
        failed=failed,
        errors=errors,
        failure_summaries=fail_lines[:_MAX_SUMMARIES],
    )


_PARSERS: dict[str, callable] = {
    "pytest": _parse_pytest,
    "jest": _parse_jest,
    "go_test": _parse_go_test,
    "cargo_test": _parse_cargo,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_test_output(raw: str, framework: str | None = None) -> TestResult:
    """Parse test runner output into a structured TestResult."""
    if not raw.strip():
        return TestResult()

    clean = _strip_ansi(raw)
    fw = framework or detect_framework(clean)
    parser = _PARSERS.get(fw, _parse_generic) if fw else _parse_generic
    result = parser(clean)
    result.raw_tail = _tail(clean)
    if result.framework is None and fw:
        result.framework = fw
    return result
