"""Tests for the quality workflow — write → review → challenge → rewrite.

Also covers harness workflow: planner → generator ↔ evaluator.
"""

from __future__ import annotations

from openmax.quality_workflow import (
    EVAL_DIMENSIONS,
    MAX_HARNESS_ROUNDS,
    QUALITY_STEPS,
    _all_above_threshold,
    _build_contract,
    _build_evaluator_prompt,
    _build_generator_prompt,
    _build_planner_prompt,
    _decide_next,
    _detect_quality_peak,
    _parse_evaluation,
    _read_challenge_report,
    _read_report,
    _weighted_average,
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


# ---------------------------------------------------------------------------
# Harness workflow tests
# ---------------------------------------------------------------------------


class TestHarnessConstants:
    def test_eval_dimensions_weights_sum_to_one(self):
        total = sum(d["weight"] for d in EVAL_DIMENSIONS.values())
        assert abs(total - 1.0) < 0.01

    def test_max_harness_rounds_positive(self):
        assert MAX_HARNESS_ROUNDS > 0


class TestParseEvaluation:
    def test_parses_valid_evaluation(self, tmp_path):
        evals = tmp_path / ".openmax" / "evaluations"
        evals.mkdir(parents=True)
        content = (
            "## Design Quality\nScore: 8/10\nGood identity.\n\n"
            "## Originality\nScore: 7/10\nSome custom choices.\n\n"
            "## Craftsmanship\nScore: 6/10\nSpacing OK.\n\n"
            "## Functionality\nScore: 9/10\nAll works.\n"
        )
        (evals / "task-round-1.md").write_text(content)
        scores = _parse_evaluation(str(tmp_path), "task", 1)
        assert scores == {
            "design_quality": 8.0,
            "originality": 7.0,
            "craftsmanship": 6.0,
            "functionality": 9.0,
        }

    def test_returns_empty_for_missing_file(self, tmp_path):
        assert _parse_evaluation(str(tmp_path), "nonexistent", 1) == {}

    def test_partial_scores(self, tmp_path):
        evals = tmp_path / ".openmax" / "evaluations"
        evals.mkdir(parents=True)
        (evals / "task-round-1.md").write_text("## Design Quality\nScore: 5/10\nOK.\n")
        scores = _parse_evaluation(str(tmp_path), "task", 1)
        assert scores == {"design_quality": 5.0}


class TestScoring:
    def test_all_above_threshold_pass(self):
        scores = {"design_quality": 8, "originality": 8, "craftsmanship": 7, "functionality": 7}
        assert _all_above_threshold(scores) is True

    def test_all_above_threshold_fail(self):
        scores = {"design_quality": 5, "originality": 8, "craftsmanship": 7, "functionality": 7}
        assert _all_above_threshold(scores) is False

    def test_missing_dimension_fails_threshold(self):
        assert _all_above_threshold({}) is False

    def test_weighted_average(self):
        scores = {"design_quality": 10, "originality": 10, "craftsmanship": 10, "functionality": 10}
        assert _weighted_average(scores) == 10.0

    def test_weighted_average_zeros(self):
        assert _weighted_average({}) == 0.0


class TestDecideNext:
    def test_accept_when_all_pass(self):
        scores = {"design_quality": 8, "originality": 8, "craftsmanship": 7, "functionality": 7}
        assert _decide_next(scores, 1, [scores]) == "accept"

    def test_refine_when_below_threshold(self):
        scores = {"design_quality": 5, "originality": 5, "craftsmanship": 5, "functionality": 5}
        assert _decide_next(scores, 1, [scores]) == "refine"

    def test_accept_at_max_rounds(self):
        scores = {"design_quality": 3, "originality": 3, "craftsmanship": 3, "functionality": 3}
        assert _decide_next(scores, MAX_HARNESS_ROUNDS, [scores]) == "accept"

    def test_pivot_on_regression(self):
        good = {"design_quality": 6, "originality": 6, "craftsmanship": 6, "functionality": 6}
        bad = {"design_quality": 4, "originality": 4, "craftsmanship": 4, "functionality": 4}
        assert _decide_next(bad, 3, [good, bad]) == "pivot"


class TestDetectQualityPeak:
    def test_single_round(self):
        assert _detect_quality_peak([{"design_quality": 5}]) == 1

    def test_peak_in_middle(self):
        history = [
            {"design_quality": 5, "originality": 5, "craftsmanship": 5, "functionality": 5},
            {"design_quality": 9, "originality": 9, "craftsmanship": 9, "functionality": 9},
            {"design_quality": 6, "originality": 6, "craftsmanship": 6, "functionality": 6},
        ]
        assert _detect_quality_peak(history) == 2

    def test_empty_history(self):
        assert _detect_quality_peak([]) == 1


class TestPromptBuilders:
    def test_planner_prompt_has_no_impl_detail_warning(self):
        prompt = _build_planner_prompt("build a todo app")
        assert "implementation details" in prompt.lower()
        assert "spec" in prompt.lower()

    def test_generator_prompt_includes_spec_and_contract(self):
        prompt = _build_generator_prompt("task", "spec text", "contract text", "")
        assert "spec text" in prompt
        assert "contract text" in prompt

    def test_generator_prompt_includes_prev_eval(self):
        prompt = _build_generator_prompt("task", "spec", "contract", "fix spacing")
        assert "fix spacing" in prompt

    def test_evaluator_prompt_has_dimensions(self):
        prompt = _build_evaluator_prompt("my-task", 1)
        assert "design_quality" in prompt
        assert "round 1" in prompt

    def test_contract_includes_spec(self):
        contract = _build_contract("task", "the spec", 1, "")
        assert "the spec" in contract
        assert "Round 1" in contract

    def test_contract_includes_prev_eval(self):
        contract = _build_contract("task", "spec", 2, "fix colors")
        assert "fix colors" in contract
