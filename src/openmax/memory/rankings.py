"""Agent ranking, evaluation, and strategy derivation functions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from openmax.memory._utils import (
    _aggregate_agent_stats,
    _coerce_int,
    _dedupe,
    _keywords,
    infer_code_scope,
)
from openmax.memory.models import (
    AgentRecommendation,
    AgentScorecard,
    MemoryEntry,
    RecommendationOfflineEval,
    RecommendationOfflineEvalReport,
    StrategyAdvice,
)


def derive_agent_rankings(
    entries: list[MemoryEntry],
    *,
    task: str,
    score_entry: Callable[[MemoryEntry, str], int],
    recency_bonus: Callable[[int, int], int],
    limit: int = 4,
) -> list[AgentRecommendation]:
    if not entries:
        return []

    task_terms = _keywords(task)
    task_scope = infer_code_scope(task)
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)

    for index, entry in enumerate(entries):
        base_weight = max(float(score_entry(entry, task)), 1.0)
        base_weight += recency_bonus(index, len(entries))

        if entry.kind == "lesson":
            _score_lesson_entry(
                entry=entry,
                task_terms=task_terms,
                task_scope=task_scope,
                base_weight=base_weight,
                scores=scores,
                reasons=reasons,
            )

        if entry.kind == "run_summary":
            _score_run_summary_entry(
                entry=entry,
                task_terms=task_terms,
                task_scope=task_scope,
                base_weight=base_weight,
                scores=scores,
                reasons=reasons,
            )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        AgentRecommendation(
            agent_type=agent,
            score=round(score, 2),
            reasons=_dedupe(reasons[agent])[:3],
        )
        for agent, score in ranked[:limit]
        if score > 0
    ]


def derive_agent_scorecard(
    entries: list[MemoryEntry],
    *,
    task: str,
    score_entry: Callable[[MemoryEntry, str], int],
    recency_bonus: Callable[[int, int], int],
    limit: int = 4,
) -> list[AgentScorecard]:
    if not entries:
        return []

    task_terms = _keywords(task)
    task_scope = infer_code_scope(task)
    aggregated: dict[str, dict[str, Any]] = {}

    for index, entry in enumerate(entries):
        agent_stats = _entry_agent_stats(entry)
        if not agent_stats:
            continue

        scope_bonus_val = _scope_bonus(entry, task_scope)
        entry_relevance = score_entry(entry, task)
        if task and entry_relevance <= 0 and scope_bonus_val <= 0:
            continue

        base_weight = max(float(entry_relevance), 1.0)
        base_weight += recency_bonus(index, len(entries))

        for stat in agent_stats:
            agent = str(stat.get("agent_type", "")).strip()
            if not agent:
                continue

            success_count = max(_coerce_int(stat.get("success_count")) or 0, 0)
            failure_count = max(_coerce_int(stat.get("failure_count")) or 0, 0)
            incomplete_count = max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
            total_count = max(
                _coerce_int(stat.get("total_count"))
                or success_count + failure_count + incomplete_count,
                0,
            )
            if total_count <= 0:
                continue

            success_rate = stat.get("success_rate")
            try:
                normalized_rate = float(success_rate)
            except (TypeError, ValueError):
                normalized_rate = success_count / total_count

            task_text = " ".join(
                str(stat.get(field, "")) for field in ("detail", "task_name", "prompt", "scope")
            )
            overlap = len(task_terms & _keywords(task_text or entry.task))
            relevance = 1 + overlap if task else 1.0
            positive_weight = success_count * (base_weight * relevance + 1.5)
            positive_weight += normalized_rate * (2 + max(scope_bonus_val, 0.0))
            negative_weight = failure_count * (base_weight * relevance + 1.0)
            negative_weight += incomplete_count * max(base_weight * 0.5, 0.5)

            record = aggregated.setdefault(
                agent,
                {
                    "agent_type": agent,
                    "recommendation_score": 0.0,
                    "success_count": 0,
                    "failure_count": 0,
                    "incomplete_count": 0,
                    "total_count": 0,
                    "reasons": [],
                },
            )
            record["recommendation_score"] += positive_weight - negative_weight + scope_bonus_val
            record["success_count"] += success_count
            record["failure_count"] += failure_count
            record["incomplete_count"] += incomplete_count
            record["total_count"] += total_count

            detail = str(stat.get("detail", "")).strip()
            if detail:
                record["reasons"].append(detail)

    ranked = sorted(
        aggregated.values(),
        key=lambda item: (
            item["recommendation_score"],
            item["success_count"],
            -item["failure_count"],
            item["total_count"],
            item["agent_type"],
        ),
        reverse=True,
    )
    return [
        AgentScorecard(
            agent_type=str(item["agent_type"]),
            recommendation_score=round(float(item["recommendation_score"]), 2),
            success_count=int(item["success_count"]),
            failure_count=int(item["failure_count"]),
            incomplete_count=int(item["incomplete_count"]),
            total_count=int(item["total_count"]),
            success_rate=round(
                int(item["success_count"]) / max(int(item["total_count"]), 1),
                2,
            ),
            reasons=_dedupe([str(reason) for reason in item["reasons"]])[:3],
        )
        for item in ranked[:limit]
    ]


def derive_strategy(
    entries: list[MemoryEntry],
    *,
    cwd: str,
    task: str,
    score_entry: Callable[[MemoryEntry, str], int],
    recency_bonus: Callable[[int, int], int],
    load_entries: Callable[[str], list[MemoryEntry]],
) -> StrategyAdvice:
    if not entries:
        return StrategyAdvice()

    ranked = (
        _rank_entries(entries, task, score_entry, recency_bonus)
        if task
        else list(reversed(entries))
    )
    focus = ranked[:8]
    fact_lines: list[str] = []
    execution_lines: list[str] = []
    risk_lines: list[str] = []

    for entry in focus:
        lessons = _entry_lessons(entry)
        if lessons:
            execution_lines.extend(f"- {lesson}" for lesson in lessons[:2])
        elif entry.summary:
            execution_lines.append(f"- Reuse pattern: {entry.summary}")

        facts = _entry_facts(entry)
        fact_lines.extend(f"- {fact}" for fact in facts[:2])

        combined_text = " ".join([entry.summary, *entry.insights, *lessons, *facts]).lower()
        risk_tokens = ("avoid", "drift", "stuck", "fail", "retry")
        if any(token in combined_text for token in risk_tokens):
            risk_lines.append(f"- Watch for: {entry.summary}")
        for signal in _entry_performance_signals(entry):
            detail = str(signal.get("detail", "")).strip()
            outcome = str(signal.get("outcome", "")).lower()
            if outcome == "negative" and detail:
                risk_lines.append(f"- {detail}")

    ranked_agents = derive_agent_rankings(
        entries,
        task=task,
        score_entry=score_entry,
        recency_bonus=recency_bonus,
    )
    agent_lines = [
        f"- Prefer {item.agent_type} for this task pattern."
        for item in ranked_agents
        if item.score > 0
    ]

    return StrategyAdvice(
        agent_lines=_dedupe(agent_lines)[:3],
        fact_lines=_dedupe(fact_lines)[:3],
        execution_lines=_dedupe(execution_lines)[:4],
        risk_lines=_dedupe(risk_lines)[:3],
    )


def evaluate_recommendations_offline(
    entries: list[MemoryEntry],
    *,
    label: str,
    history_selector: Callable[[list[MemoryEntry], int, MemoryEntry], list[MemoryEntry]],
    predictor: Callable[[list[MemoryEntry], str], str | None],
) -> RecommendationOfflineEval:
    run_entries = [
        entry for entry in entries if entry.kind == "run_summary" and _entry_agent_stats(entry)
    ]
    if not run_entries:
        return RecommendationOfflineEval(
            total_runs=0,
            evaluated_runs=0,
            covered_runs=0,
            hit_runs=0,
            coverage=0.0,
            hit_rate=0.0,
            average_completion_pct=0.0,
            average_failure_rate=0.0,
            label=label,
        )

    covered_runs = 0
    hit_runs = 0
    completion_total = 0.0
    failure_total = 0.0

    for index, entry in enumerate(run_entries):
        history = history_selector(run_entries, index, entry)
        if not history:
            continue

        predicted_agent = predictor(history, entry.task)
        if not predicted_agent:
            continue

        covered_runs += 1
        expected_agents = _expected_agents_for_entry(entry)
        if predicted_agent in expected_agents:
            hit_runs += 1

        completion_total += float(entry.completion_pct or 0)
        failure_total += _observed_failure_rate(entry, predicted_agent)

    evaluated_runs = max(len(run_entries) - 1, 0)
    coverage = round(covered_runs / evaluated_runs, 2) if evaluated_runs else 0.0
    hit_rate = round(hit_runs / covered_runs, 2) if covered_runs else 0.0
    average_completion_pct = round(completion_total / covered_runs, 2) if covered_runs else 0.0
    average_failure_rate = round(failure_total / covered_runs, 2) if covered_runs else 0.0
    return RecommendationOfflineEval(
        total_runs=len(run_entries),
        evaluated_runs=evaluated_runs,
        covered_runs=covered_runs,
        hit_runs=hit_runs,
        coverage=coverage,
        hit_rate=hit_rate,
        average_completion_pct=average_completion_pct,
        average_failure_rate=average_failure_rate,
        label=label,
    )


def evaluate_recommendations_against_baseline(
    entries: list[MemoryEntry],
    *,
    cwd: str,
    is_relevant: Callable[[MemoryEntry, str], bool],
    top_scorecard_agent: Callable[[str, str, list[MemoryEntry]], str | None],
) -> RecommendationOfflineEvalReport:
    strategy = evaluate_recommendations_offline(
        entries,
        label="strategy",
        history_selector=lambda run_entries, index, entry: [
            candidate for candidate in run_entries[:index] if is_relevant(candidate, entry.task)
        ],
        predictor=lambda history, task: top_scorecard_agent(cwd, task, history),
    )
    baseline = evaluate_recommendations_offline(
        entries,
        label="global_top_agent",
        history_selector=lambda run_entries, index, _entry: run_entries[:index],
        predictor=lambda history, _task: _top_global_agent(history),
    )
    return RecommendationOfflineEvalReport(
        strategy=strategy,
        baseline=baseline,
        coverage_delta=round(strategy.coverage - baseline.coverage, 2),
        hit_rate_lift=round(strategy.hit_rate - baseline.hit_rate, 2),
        completion_pct_delta=round(
            strategy.average_completion_pct - baseline.average_completion_pct,
            2,
        ),
        failure_rate_delta=round(
            strategy.average_failure_rate - baseline.average_failure_rate,
            2,
        ),
    )


# ── Shared helpers used by both rankings and store ────────────────


def _scope_bonus(entry: MemoryEntry, task_scope: list[str]) -> float:
    if not task_scope:
        return 0.0
    entry_scope = entry.metadata.get("code_scope", [])
    if not isinstance(entry_scope, list):
        return 0.0
    overlap = len(set(task_scope) & set(str(item) for item in entry_scope))
    if overlap >= 3:
        return 4.0
    if overlap == 2:
        return 2.5
    if overlap == 1:
        return 1.0
    return -0.5


def _rank_entries(
    entries: list[MemoryEntry],
    task: str,
    score_entry: Callable[[MemoryEntry, str], int],
    recency_bonus: Callable[[int, int], int],
) -> list[MemoryEntry]:
    indexed = list(enumerate(entries))
    ranked = sorted(
        indexed,
        key=lambda item: (
            score_entry(item[1], task),
            recency_bonus(item[0], len(indexed)),
            item[1].created_at,
        ),
        reverse=True,
    )
    return [entry for _index, entry in ranked]


def _entry_lessons(entry: MemoryEntry) -> list[str]:
    if entry.lessons:
        return entry.lessons
    if entry.kind == "lesson" and entry.summary:
        return [entry.summary]
    return []


def _entry_facts(entry: MemoryEntry) -> list[str]:
    if entry.workspace_facts:
        return entry.workspace_facts

    facts: list[str] = []
    for insight in entry.insights:
        normalized = insight.strip()
        if normalized.startswith("Completed '"):
            continue
        facts.append(normalized)

    scope = entry.metadata.get("code_scope", [])
    if isinstance(scope, list) and scope:
        facts.append("Relevant scope: " + ", ".join(str(item) for item in scope[:4]))
    return _dedupe(facts)


def _entry_performance_signals(entry: MemoryEntry) -> list[dict[str, Any]]:
    if entry.performance_signals:
        return entry.performance_signals

    subtasks = entry.metadata.get("subtasks", [])
    if not isinstance(subtasks, list):
        return []

    signals: list[dict[str, Any]] = []
    for task_info in subtasks:
        if not isinstance(task_info, dict):
            continue
        agent = str(task_info.get("agent_type", "")).strip()
        status = str(task_info.get("status", "")).strip().lower()
        task_name = str(task_info.get("name", "")).strip()
        prompt = str(task_info.get("prompt", "")).strip()
        if not agent and not status:
            continue
        if status == "done":
            verb = "completed"
        elif status in {"error", "failed"}:
            verb = "failed on"
        else:
            verb = "worked on"
        signals.append(
            {
                "agent_type": agent,
                "status": status,
                "task_name": task_name,
                "prompt": prompt,
                "outcome": (
                    "positive"
                    if status == "done"
                    else "negative"
                    if status in {"error", "failed"}
                    else "neutral"
                ),
                "detail": f"{agent} {verb} '{task_name or 'unknown'}'".strip(),
            }
        )
    return signals


def _entry_agent_stats(entry: MemoryEntry) -> list[dict[str, Any]]:
    if entry.agent_stats:
        return entry.agent_stats

    performance_signals = _entry_performance_signals(entry)
    if not performance_signals:
        return []
    return _aggregate_agent_stats(
        performance_signals=performance_signals,
        completion_pct=entry.completion_pct,
    )


def _expected_agents_for_entry(entry: MemoryEntry) -> set[str]:
    ranked: list[tuple[tuple[float, int, int, int], str]] = []
    for stat in _entry_agent_stats(entry):
        agent = str(stat.get("agent_type", "")).strip()
        if not agent:
            continue
        success_count = max(_coerce_int(stat.get("success_count")) or 0, 0)
        failure_count = max(_coerce_int(stat.get("failure_count")) or 0, 0)
        incomplete_count = max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
        total_count = max(
            _coerce_int(stat.get("total_count"))
            or success_count + failure_count + incomplete_count,
            0,
        )
        if total_count <= 0:
            continue
        success_rate = stat.get("success_rate")
        try:
            normalized_rate = float(success_rate)
        except (TypeError, ValueError):
            normalized_rate = success_count / total_count
        score = (
            normalized_rate,
            success_count,
            -failure_count,
            -incomplete_count,
        )
        ranked.append((score, agent))

    if not ranked:
        return set()
    best_score = max(score for score, _agent in ranked)
    return {agent for score, agent in ranked if score == best_score}


def _observed_failure_rate(entry: MemoryEntry, agent_type: str) -> float:
    for stat in _entry_agent_stats(entry):
        agent = str(stat.get("agent_type", "")).strip()
        if agent != agent_type:
            continue
        failure_count = max(_coerce_int(stat.get("failure_count")) or 0, 0)
        incomplete_count = max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
        total_count = max(
            _coerce_int(stat.get("total_count")) or failure_count + incomplete_count,
            0,
        )
        if total_count <= 0:
            return 0.0
        return round((failure_count + incomplete_count) / total_count, 2)
    return 1.0


def _top_global_agent(history: list[MemoryEntry]) -> str | None:
    aggregated: dict[str, dict[str, int]] = {}
    for entry in history:
        for stat in _entry_agent_stats(entry):
            agent = str(stat.get("agent_type", "")).strip()
            if not agent:
                continue
            record = aggregated.setdefault(
                agent,
                {
                    "success_count": 0,
                    "failure_count": 0,
                    "incomplete_count": 0,
                    "total_count": 0,
                },
            )
            record["success_count"] += max(_coerce_int(stat.get("success_count")) or 0, 0)
            record["failure_count"] += max(_coerce_int(stat.get("failure_count")) or 0, 0)
            record["incomplete_count"] += max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
            record["total_count"] += max(
                _coerce_int(stat.get("total_count"))
                or (
                    max(_coerce_int(stat.get("success_count")) or 0, 0)
                    + max(_coerce_int(stat.get("failure_count")) or 0, 0)
                    + max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
                ),
                0,
            )

    if not aggregated:
        return None

    ranked = sorted(
        aggregated.items(),
        key=lambda item: (
            item[1]["success_count"] / max(item[1]["total_count"], 1),
            item[1]["success_count"],
            -item[1]["failure_count"],
            -item[1]["incomplete_count"],
            item[1]["total_count"],
            item[0],
        ),
        reverse=True,
    )
    return ranked[0][0]


def _score_lesson_entry(
    *,
    entry: MemoryEntry,
    task_terms: set[str],
    task_scope: list[str],
    base_weight: float,
    scores: dict[str, float],
    reasons: dict[str, list[str]],
) -> None:
    combined = " ".join(
        [entry.task, entry.summary, *entry.insights, *_entry_lessons(entry)]
    ).lower()
    scope_bonus_val = _scope_bonus(entry, task_scope)
    for agent in ("claude-code", "codex", "opencode", "generic"):
        mentions_agent = agent in combined
        if not mentions_agent:
            continue
        relevance = 1.0
        if task_terms and task_terms & _keywords(combined):
            relevance += 1.5
        if "prefer" in combined or "best" in combined or "fastest" in combined:
            scores[agent] += base_weight * relevance + 2 + scope_bonus_val
            reasons[agent].append(entry.summary)
        elif "avoid" in combined or "drift" in combined or "fail" in combined:
            scores[agent] -= base_weight * relevance + 2 + scope_bonus_val
        else:
            scores[agent] += base_weight * 0.5 + scope_bonus_val * 0.25


def _score_run_summary_entry(
    *,
    entry: MemoryEntry,
    task_terms: set[str],
    task_scope: list[str],
    base_weight: float,
    scores: dict[str, float],
    reasons: dict[str, list[str]],
) -> None:
    scope_bonus_val = _scope_bonus(entry, task_scope)
    agent_stats = _entry_agent_stats(entry)
    if agent_stats:
        for stat in agent_stats:
            agent = str(stat.get("agent_type", "")).strip()
            if not agent:
                continue
            task_text = " ".join(
                str(stat.get(field, "")) for field in ("detail", "task_name", "prompt", "scope")
            )
            overlap = len(task_terms & _keywords(task_text or entry.task))
            relevance = 1 + overlap
            success_count = max(_coerce_int(stat.get("success_count")) or 0, 0)
            failure_count = max(_coerce_int(stat.get("failure_count")) or 0, 0)
            incomplete_count = max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
            total_count = max(
                _coerce_int(stat.get("total_count"))
                or success_count + failure_count + incomplete_count,
                0,
            )
            success_rate = stat.get("success_rate")
            try:
                normalized_rate = float(success_rate)
            except (TypeError, ValueError):
                normalized_rate = success_count / total_count if total_count else 0.0

            positive_weight = success_count * (base_weight * relevance + 1.5)
            positive_weight += normalized_rate * (2 + max(scope_bonus_val, 0.0))
            negative_weight = failure_count * (base_weight * relevance + 1.0)
            negative_weight += incomplete_count * max(base_weight * 0.5, 0.5)
            scores[agent] += positive_weight - negative_weight + scope_bonus_val

            if success_count > 0:
                detail = str(stat.get("detail", "")).strip()
                reasons[agent].append(
                    detail
                    or (
                        f"{agent} succeeded on {success_count} of "
                        f"{total_count or success_count} similar subtasks"
                    )
                )
        return

    for signal in _entry_performance_signals(entry):
        agent = str(signal.get("agent_type", "")).strip()
        if not agent:
            continue
        status = str(signal.get("status", "")).lower()
        task_text = " ".join(
            str(signal.get(field, "")) for field in ("task_name", "prompt", "detail")
        )
        overlap = len(task_terms & _keywords(task_text or entry.task))
        relevance = 1 + overlap
        if status == "done":
            scores[agent] += base_weight * relevance + 2 + scope_bonus_val
            reasons[agent].append(
                str(signal.get("detail", "")).strip()
                or f"{agent} completed '{signal.get('task_name', 'unknown')}' successfully"
            )
        elif status in {"error", "failed"}:
            scores[agent] -= base_weight * relevance + 1 + scope_bonus_val
