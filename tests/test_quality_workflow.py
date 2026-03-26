"""Tests for the quality workflow — write → review → challenge → rewrite."""

from __future__ import annotations

from openmax.quality_workflow import (
    QUALITY_STEPS,
    _read_challenge_report,
    _read_report,
)


def test_only_committing_steps_have_commits_true():
    for step in QUALITY_STEPS:
        if step.step_type in ("write", "rewrite"):
            assert step.commits is True
        else:
            assert step.commits is False


def test_rewrite_template_has_challenge_feedback():
    rewrite = QUALITY_STEPS[-1]
    assert "{challenge_feedback}" in rewrite.prompt_template


def test_rewrite_template_has_violations_block():
    rewrite = QUALITY_STEPS[-1]
    assert "{violations_block}" in rewrite.prompt_template


def test_rewrite_has_ranked_objectives():
    template = QUALITY_STEPS[-1].prompt_template
    mandatory_pos = template.find("MANDATORY")
    conditional_pos = template.find("CONDITIONAL")
    aspirational_pos = template.find("ASPIRATIONAL")
    assert mandatory_pos < conditional_pos < aspirational_pos


def test_review_template_has_violations_block():
    review = QUALITY_STEPS[1]
    assert "{violations_block}" in review.prompt_template


def test_read_report_returns_content(tmp_path):
    reports = tmp_path / ".openmax" / "reports"
    reports.mkdir(parents=True)
    (reports / "task-review.md").write_text("good code")
    assert _read_report(str(tmp_path), "task") == "good code"


def test_read_report_returns_fallback(tmp_path):
    result = _read_report(str(tmp_path), "missing")
    assert "No review report" in result


def test_read_challenge_report_returns_content(tmp_path):
    reports = tmp_path / ".openmax" / "reports"
    reports.mkdir(parents=True)
    (reports / "task-challenge.md").write_text("simpler design")
    assert _read_challenge_report(str(tmp_path), "task") == "simpler design"


def test_read_challenge_report_returns_fallback(tmp_path):
    result = _read_challenge_report(str(tmp_path), "missing")
    assert "No challenge report" in result
