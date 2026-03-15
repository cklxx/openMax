from openmax.doctor import CheckResult, render_results, run_checks


def test_render_results_all_ok():
    results = [CheckResult("Python", ok=True, version="3.11"), CheckResult("Kaku", ok=True)]
    lines, issues = render_results(results)
    assert issues == 0
    assert "All checks passed" in "\n".join(lines)


def test_render_results_with_failures():
    results = [CheckResult("codex", ok=False, fix_hint="npm install -g @openai/codex")]
    lines, issues = render_results(results)
    assert issues == 1
    assert "Fix:" in "\n".join(lines)


def test_run_checks_returns_list():
    results = run_checks()
    assert len(results) >= 5
    names = [r.name for r in results]
    assert "Python" in names
    assert "claude" in names


def test_python_check_passes():
    from openmax.doctor import _check_python

    result = _check_python()
    assert result.ok  # we're running Python 3.10+
