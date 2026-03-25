"""Tests for AST-based style violation checker."""

from __future__ import annotations

from openmax.style_check import StyleViolation, check_style_violations, format_violations


def test_clean_code_no_violations(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def short():\n    return 1\n")
    assert check_style_violations([str(f)]) == []


def test_detects_long_function(tmp_path):
    f = tmp_path / "long.py"
    body = "\n".join(f"    x{i} = {i}" for i in range(20))
    f.write_text(f"def too_long():\n{body}\n")
    violations = check_style_violations([str(f)])
    assert len(violations) == 1
    assert violations[0].function == "too_long"
    assert violations[0].metric == "function_length"
    assert violations[0].value == 20
    assert violations[0].threshold == 15


def test_custom_threshold(tmp_path):
    f = tmp_path / "medium.py"
    body = "\n".join(f"    x{i} = {i}" for i in range(8))
    f.write_text(f"def medium():\n{body}\n")
    assert check_style_violations([str(f)], max_function_lines=5) != []
    assert check_style_violations([str(f)], max_function_lines=10) == []


def test_syntax_error_handled(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def broken(\n")
    violations = check_style_violations([str(f)])
    assert len(violations) == 1
    assert violations[0].metric == "syntax_error"
    assert violations[0].function == "<module>"


def test_async_function_detected(tmp_path):
    f = tmp_path / "async_long.py"
    body = "\n".join(f"    x{i} = {i}" for i in range(20))
    f.write_text(f"async def long_async():\n{body}\n")
    violations = check_style_violations([str(f)])
    assert len(violations) == 1
    assert violations[0].function == "long_async"


def test_empty_file_list():
    assert check_style_violations([]) == []


def test_nonexistent_file():
    assert check_style_violations(["/nonexistent/path.py"]) == []


def test_violation_rendering_uses_code_blocks(tmp_path):
    f = tmp_path / "long.py"
    body = "\n".join(f"    x{i} = {i}" for i in range(20))
    f.write_text(f"def too_long():\n{body}\n")
    violations = check_style_violations([str(f)])
    rendered = format_violations(violations)
    assert "```" in rendered
    assert "`too_long`" in rendered


def test_format_violations_empty():
    assert format_violations([]) == ""


def test_format_violations_syntax_error():
    v = StyleViolation("bad.py", "<module>", 1, "syntax_error", 0, 0)
    rendered = format_violations([v])
    assert "syntax error" in rendered
    assert "```" in rendered
