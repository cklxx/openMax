"""Workspace memory store for reusable lessons and run summaries."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

MemoryKind = Literal["lesson", "run_summary"]
_MAX_ENTRIES_PER_WORKSPACE = 50
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "build",
    "make",
    "into",
    "when",
    "your",
    "task",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_memory_dir() -> Path:
    return Path.home() / ".openmax" / "memory"


@dataclass
class MemoryEntry:
    memory_id: str
    created_at: str
    kind: MemoryKind
    task: str
    summary: str
    insights: list[str] = field(default_factory=list)
    workspace_facts: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    performance_signals: list[dict[str, Any]] = field(default_factory=list)
    agent_stats: list[dict[str, Any]] = field(default_factory=list)
    confidence: int | None = None
    completion_pct: int | None = None
    source: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> "MemoryEntry":
        kind = item.get("kind", "lesson")
        if kind not in {"lesson", "run_summary"}:
            kind = "lesson"
        metadata = item.get("metadata", {})
        return cls(
            memory_id=str(item.get("memory_id", uuid.uuid4().hex)),
            created_at=str(item.get("created_at", utc_now_iso())),
            kind=kind,
            task=str(item.get("task", "")).strip(),
            summary=str(item.get("summary", "")).strip(),
            insights=_coerce_string_list(item.get("insights")),
            workspace_facts=_coerce_string_list(item.get("workspace_facts")),
            lessons=_coerce_string_list(item.get("lessons")),
            performance_signals=_coerce_signal_list(item.get("performance_signals")),
            agent_stats=_coerce_stat_list(item.get("agent_stats")),
            confidence=_coerce_int(item.get("confidence")),
            completion_pct=_coerce_int(item.get("completion_pct")),
            source=str(item.get("source", "system")).strip() or "system",
            metadata=metadata if isinstance(metadata, dict) else {},
        )


@dataclass
class MemoryContext:
    text: str
    matched_entries: int


@dataclass
class StrategyAdvice:
    agent_lines: list[str] = field(default_factory=list)
    fact_lines: list[str] = field(default_factory=list)
    execution_lines: list[str] = field(default_factory=list)
    risk_lines: list[str] = field(default_factory=list)


@dataclass
class AgentRecommendation:
    agent_type: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class AgentScorecard:
    agent_type: str
    recommendation_score: float
    success_count: int
    failure_count: int
    incomplete_count: int
    total_count: int
    success_rate: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RecommendationOfflineEval:
    total_runs: int
    evaluated_runs: int
    covered_runs: int
    hit_runs: int
    coverage: float
    hit_rate: float
    average_completion_pct: float
    average_failure_rate: float


class MemoryStore:
    """Store and retrieve workspace-scoped memory entries."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or default_memory_dir()).expanduser()

    def record_lesson(
        self,
        *,
        cwd: str,
        task: str,
        lesson: str,
        rationale: str = "",
        confidence: int | None = None,
        source: str = "agent",
    ) -> MemoryEntry:
        insights = [rationale.strip()] if rationale.strip() else []
        entry = MemoryEntry(
            memory_id=uuid.uuid4().hex,
            created_at=utc_now_iso(),
            kind="lesson",
            task=task,
            summary=lesson.strip(),
            insights=insights,
            lessons=[lesson.strip()],
            confidence=confidence,
            source=source,
            metadata={
                "code_scope": infer_code_scope(task, lesson, rationale),
            },
        )
        self._append_entry(cwd, entry)
        return entry

    def record_run_summary(
        self,
        *,
        cwd: str,
        task: str,
        notes: str,
        completion_pct: int,
        subtasks: list[dict[str, Any]],
        anchors: list[dict[str, Any]],
    ) -> MemoryEntry:
        insights: list[str] = []

        done_tasks = [task_info for task_info in subtasks if task_info.get("status") == "done"]
        if done_tasks:
            insights.extend(
                "Completed "
                f"'{task_info.get('name', 'unknown')}' "
                f"with {task_info.get('agent_type', 'unknown')}"
                for task_info in done_tasks[:4]
            )

        anchor_summaries = [
            str(anchor.get("summary", "")).strip()
            for anchor in anchors[-3:]
            if str(anchor.get("summary", "")).strip()
        ]
        insights.extend(anchor_summaries[:3])

        entry = MemoryEntry(
            memory_id=uuid.uuid4().hex,
            created_at=utc_now_iso(),
            kind="run_summary",
            task=task,
            summary=notes.strip(),
            insights=insights[:6],
            workspace_facts=_derive_workspace_facts(
                task=task,
                anchor_summaries=anchor_summaries,
                subtasks=subtasks,
            ),
            performance_signals=_derive_performance_signals(
                subtasks=subtasks,
                completion_pct=completion_pct,
            ),
            agent_stats=_derive_agent_stats(
                subtasks=subtasks,
                completion_pct=completion_pct,
            ),
            completion_pct=completion_pct,
            source="report_completion",
            metadata={
                "subtasks": subtasks[:12],
                "code_scope": infer_code_scope(
                    task,
                    notes,
                    *anchor_summaries,
                    subtasks=subtasks,
                ),
            },
        )
        self._append_entry(cwd, entry)
        return entry

    def load_entries(self, cwd: str) -> list[MemoryEntry]:
        path = self._workspace_path(cwd)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            return []
        return [MemoryEntry.from_payload(item) for item in entries if isinstance(item, dict)]

    def render_workspace_memories(self, cwd: str, limit: int = 10) -> list[str]:
        entries = list(reversed(self.load_entries(cwd)))[0:limit]
        lines: list[str] = []
        advice = self.derive_strategy(cwd=cwd, task="")
        if advice.agent_lines or advice.fact_lines or advice.execution_lines or advice.risk_lines:
            lines.append("Strategy:")
            lines.extend(advice.agent_lines[:2])
            lines.extend(advice.fact_lines[:2])
            lines.extend(advice.execution_lines[:2])
            lines.extend(advice.risk_lines[:2])
        scorecard = self.derive_agent_scorecard(cwd=cwd, task="", limit=3)
        if scorecard:
            lines.append("Agent scorecard:")
            for item in scorecard:
                line = (
                    f"- {item.agent_type}: "
                    f"{item.success_count}/{item.total_count} succeeded"
                )
                if item.failure_count:
                    line += f", {item.failure_count} failed"
                if item.incomplete_count:
                    line += f", {item.incomplete_count} incomplete"
                line += f", score {item.recommendation_score:.1f}"
                lines.append(line)
        for entry in entries:
            suffix = f" ({entry.kind})"
            if entry.completion_pct is not None:
                suffix += f" [{entry.completion_pct}%]"
            scope = entry.metadata.get("code_scope", [])
            if isinstance(scope, list) and scope:
                suffix += f" <{', '.join(scope[:3])}>"
            lines.append(f"- {entry.summary}{suffix}")
            for insight in entry.insights[:2]:
                lines.append(f"  {insight}")
            for fact in self._entry_facts(entry)[:1]:
                lines.append(f"  fact: {fact}")
            for signal in self._entry_performance_signals(entry)[:1]:
                detail = str(signal.get("detail", "")).strip()
                if detail:
                    lines.append(f"  signal: {detail}")
        return lines

    def build_context(self, *, cwd: str, task: str, limit: int = 4) -> MemoryContext | None:
        entries = self.load_entries(cwd)
        if not entries:
            return None

        ranked = self._rank_entries(entries, task)
        ranked_scores = [(entry, self._score_entry(entry, task)) for entry in ranked]
        selected = [entry for entry, score in ranked_scores if score > 0][:limit]
        if not selected:
            selected = ranked[: min(limit, len(ranked))]

        lines = ["Learned memory for this workspace:"]
        task_scope = infer_code_scope(task)
        if task_scope:
            lines.append("Relevant code scope: " + ", ".join(task_scope[:5]))
        advice = self.derive_strategy(cwd=cwd, task=task, entries=entries)
        if advice.agent_lines:
            lines.append("Recommended agent choices:")
            lines.extend(advice.agent_lines[:3])
        if advice.fact_lines:
            lines.append("Workspace facts:")
            lines.extend(advice.fact_lines[:3])
        if advice.execution_lines:
            lines.append("Execution guidance:")
            lines.extend(advice.execution_lines[:3])
        if advice.risk_lines:
            lines.append("Known risks:")
            lines.extend(advice.risk_lines[:2])
        for entry in selected:
            prefix = "lesson" if entry.kind == "lesson" else "run"
            detail = f"- [{prefix}] {entry.summary}"
            if entry.confidence is not None:
                detail += f" (confidence {entry.confidence}/10)"
            if entry.completion_pct is not None:
                detail += f" [{entry.completion_pct}%]"
            lines.append(detail)
            for insight in entry.insights[:2]:
                lines.append(f"  {insight}")

        return MemoryContext(text="\n".join(lines), matched_entries=len(selected))

    def derive_agent_scorecard(
        self,
        *,
        cwd: str,
        task: str,
        entries: list[MemoryEntry] | None = None,
        limit: int = 4,
    ) -> list[AgentScorecard]:
        records = list(entries if entries is not None else self.load_entries(cwd))
        if not records:
            return []

        task_terms = _keywords(task)
        task_scope = infer_code_scope(task)
        aggregated: dict[str, dict[str, Any]] = {}

        for index, entry in enumerate(records):
            agent_stats = self._entry_agent_stats(entry)
            if not agent_stats:
                continue

            scope_bonus = self._scope_bonus(entry, task_scope)
            entry_relevance = self._score_entry(entry, task)
            if task and entry_relevance <= 0 and scope_bonus <= 0:
                continue

            base_weight = max(float(entry_relevance), 1.0)
            base_weight += self._recency_bonus(index, len(records))

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
                    str(stat.get(field, ""))
                    for field in ("detail", "task_name", "prompt", "scope")
                )
                overlap = len(task_terms & _keywords(task_text or entry.task))
                relevance = 1 + overlap if task else 1.0
                positive_weight = success_count * (base_weight * relevance + 1.5)
                positive_weight += normalized_rate * (2 + max(scope_bonus, 0.0))
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
                record["recommendation_score"] += positive_weight - negative_weight + scope_bonus
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
        self,
        *,
        cwd: str,
        task: str,
        entries: list[MemoryEntry] | None = None,
    ) -> StrategyAdvice:
        records = list(entries if entries is not None else self.load_entries(cwd))
        if not records:
            return StrategyAdvice()

        ranked = self._rank_entries(records, task) if task else list(reversed(records))
        focus = ranked[:8]
        fact_lines: list[str] = []
        execution_lines: list[str] = []
        risk_lines: list[str] = []

        for entry in focus:
            lessons = self._entry_lessons(entry)
            if lessons:
                execution_lines.extend(f"- {lesson}" for lesson in lessons[:2])
            elif entry.summary:
                execution_lines.append(f"- Reuse pattern: {entry.summary}")

            facts = self._entry_facts(entry)
            fact_lines.extend(f"- {fact}" for fact in facts[:2])

            combined_text = " ".join(
                [entry.summary, *entry.insights, *lessons, *facts]
            ).lower()
            risk_tokens = ("avoid", "drift", "stuck", "fail", "retry")
            if any(token in combined_text for token in risk_tokens):
                risk_lines.append(f"- Watch for: {entry.summary}")
            for signal in self._entry_performance_signals(entry):
                detail = str(signal.get("detail", "")).strip()
                outcome = str(signal.get("outcome", "")).lower()
                if outcome == "negative" and detail:
                    risk_lines.append(f"- {detail}")

        ranked_agents = self.derive_agent_rankings(cwd=cwd, task=task, entries=records)
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

    def evaluate_recommendations_offline(self, *, cwd: str) -> RecommendationOfflineEval:
        records = self.load_entries(cwd)
        run_entries = [
            entry
            for entry in records
            if entry.kind == "run_summary" and self._entry_agent_stats(entry)
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
            )

        covered_runs = 0
        hit_runs = 0
        completion_total = 0.0
        failure_total = 0.0

        for index, entry in enumerate(run_entries):
            history = [
                candidate
                for candidate in run_entries[:index]
                if self._is_relevant_scorecard_entry(candidate, entry.task)
            ]
            if not history:
                continue

            scorecard = self.derive_agent_scorecard(
                cwd=cwd,
                task=entry.task,
                entries=history,
                limit=1,
            )
            if not scorecard:
                continue

            covered_runs += 1
            top_agent = scorecard[0].agent_type
            expected_agents = self._expected_agents_for_entry(entry)
            if top_agent in expected_agents:
                hit_runs += 1

            completion_total += float(entry.completion_pct or 0)
            failure_total += self._observed_failure_rate(entry, top_agent)

        evaluated_runs = max(len(run_entries) - 1, 0)
        coverage = round(covered_runs / evaluated_runs, 2) if evaluated_runs else 0.0
        hit_rate = round(hit_runs / covered_runs, 2) if covered_runs else 0.0
        average_completion_pct = (
            round(completion_total / covered_runs, 2) if covered_runs else 0.0
        )
        average_failure_rate = (
            round(failure_total / covered_runs, 2) if covered_runs else 0.0
        )
        return RecommendationOfflineEval(
            total_runs=len(run_entries),
            evaluated_runs=evaluated_runs,
            covered_runs=covered_runs,
            hit_runs=hit_runs,
            coverage=coverage,
            hit_rate=hit_rate,
            average_completion_pct=average_completion_pct,
            average_failure_rate=average_failure_rate,
        )

    def derive_agent_rankings(
        self,
        *,
        cwd: str,
        task: str,
        entries: list[MemoryEntry] | None = None,
        limit: int = 4,
    ) -> list[AgentRecommendation]:
        records = list(entries if entries is not None else self.load_entries(cwd))
        if not records:
            return []

        task_terms = _keywords(task)
        task_scope = infer_code_scope(task)
        scores: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)

        for index, entry in enumerate(records):
            base_weight = max(float(self._score_entry(entry, task)), 1.0)
            base_weight += self._recency_bonus(index, len(records))

            if entry.kind == "lesson":
                self._score_lesson_entry(
                    entry=entry,
                    task_terms=task_terms,
                    task_scope=task_scope,
                    base_weight=base_weight,
                    scores=scores,
                    reasons=reasons,
                )

            if entry.kind == "run_summary":
                self._score_run_summary_entry(
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

    def _append_entry(self, cwd: str, entry: MemoryEntry) -> None:
        payload = self._load_workspace_payload(cwd)
        entries = payload.setdefault("entries", [])
        entries.append(asdict(entry))
        payload["cwd"] = str(Path(cwd).resolve())
        payload["updated_at"] = utc_now_iso()
        if len(entries) > _MAX_ENTRIES_PER_WORKSPACE:
            payload["entries"] = entries[-_MAX_ENTRIES_PER_WORKSPACE:]

        path = self._workspace_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_workspace_payload(self, cwd: str) -> dict[str, Any]:
        path = self._workspace_path(cwd)
        if not path.exists():
            return {"cwd": str(Path(cwd).resolve()), "entries": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _workspace_path(self, cwd: str) -> Path:
        resolved = str(Path(cwd).resolve())
        digest = hashlib.md5(resolved.encode(), usedforsecurity=False).hexdigest()[:16]
        return self.base_dir / f"workspace_{digest}.json"

    def _score_entry(self, entry: MemoryEntry, task: str) -> int:
        task_terms = _keywords(task)
        entry_text = " ".join(
            [
                entry.task,
                entry.summary,
                " ".join(entry.insights),
                " ".join(entry.workspace_facts),
                " ".join(entry.lessons),
                " ".join(
                    str(signal.get("detail", "")) for signal in entry.performance_signals
                ),
                " ".join(str(stat.get("detail", "")) for stat in self._entry_agent_stats(entry)),
            ]
        )
        entry_terms = _keywords(entry_text)
        overlap = len(task_terms & entry_terms)
        score = overlap
        if entry.kind == "lesson":
            score += 2
        if entry.completion_pct:
            score += max(entry.completion_pct // 25, 0)
        return score

    def _score_lesson_entry(
        self,
        *,
        entry: MemoryEntry,
        task_terms: set[str],
        task_scope: list[str],
        base_weight: float,
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        combined = " ".join(
            [entry.task, entry.summary, *entry.insights, *self._entry_lessons(entry)]
        ).lower()
        scope_bonus = self._scope_bonus(entry, task_scope)
        for agent in ("claude-code", "codex", "opencode", "generic"):
            mentions_agent = agent in combined
            if not mentions_agent:
                continue
            relevance = 1.0
            if task_terms and task_terms & _keywords(combined):
                relevance += 1.5
            if "prefer" in combined or "best" in combined or "fastest" in combined:
                scores[agent] += base_weight * relevance + 2 + scope_bonus
                reasons[agent].append(entry.summary)
            elif "avoid" in combined or "drift" in combined or "fail" in combined:
                scores[agent] -= base_weight * relevance + 2 + scope_bonus
            else:
                scores[agent] += base_weight * 0.5 + scope_bonus * 0.25

    def _score_run_summary_entry(
        self,
        *,
        entry: MemoryEntry,
        task_terms: set[str],
        task_scope: list[str],
        base_weight: float,
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        scope_bonus = self._scope_bonus(entry, task_scope)
        agent_stats = self._entry_agent_stats(entry)
        if agent_stats:
            for stat in agent_stats:
                agent = str(stat.get("agent_type", "")).strip()
                if not agent:
                    continue
                task_text = " ".join(
                    str(stat.get(field, ""))
                    for field in ("detail", "task_name", "prompt", "scope")
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
                    normalized_rate = (
                        success_count / total_count if total_count else 0.0
                    )

                positive_weight = success_count * (base_weight * relevance + 1.5)
                positive_weight += normalized_rate * (2 + max(scope_bonus, 0.0))
                negative_weight = failure_count * (base_weight * relevance + 1.0)
                negative_weight += incomplete_count * max(base_weight * 0.5, 0.5)
                scores[agent] += positive_weight - negative_weight + scope_bonus

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

        for signal in self._entry_performance_signals(entry):
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
                scores[agent] += base_weight * relevance + 2 + scope_bonus
                reasons[agent].append(
                    str(signal.get("detail", "")).strip()
                    or f"{agent} completed '{signal.get('task_name', 'unknown')}' successfully"
                )
            elif status in {"error", "failed"}:
                scores[agent] -= base_weight * relevance + 1 + scope_bonus

    def _scope_bonus(self, entry: MemoryEntry, task_scope: list[str]) -> float:
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

    def _rank_entries(self, entries: list[MemoryEntry], task: str) -> list[MemoryEntry]:
        indexed = list(enumerate(entries))
        ranked = sorted(
            indexed,
            key=lambda item: (
                self._score_entry(item[1], task),
                self._recency_bonus(item[0], len(indexed)),
                item[1].created_at,
            ),
            reverse=True,
        )
        return [entry for _index, entry in ranked]

    @staticmethod
    def _recency_bonus(index: int, total: int) -> int:
        # Newer entries arrive later in the append-only list.
        distance = total - index
        if distance <= 3:
            return 3
        if distance <= 8:
            return 2
        if distance <= 16:
            return 1
        return 0

    @staticmethod
    def _entry_lessons(entry: MemoryEntry) -> list[str]:
        if entry.lessons:
            return entry.lessons
        if entry.kind == "lesson" and entry.summary:
            return [entry.summary]
        return []

    @staticmethod
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

    @staticmethod
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
                        else "negative" if status in {"error", "failed"} else "neutral"
                    ),
                    "detail": f"{agent} {verb} '{task_name or 'unknown'}'".strip(),
                }
            )
        return signals

    @staticmethod
    def _entry_agent_stats(entry: MemoryEntry) -> list[dict[str, Any]]:
        if entry.agent_stats:
            return entry.agent_stats

        performance_signals = MemoryStore._entry_performance_signals(entry)
        if not performance_signals:
            return []
        return _aggregate_agent_stats(
            performance_signals=performance_signals,
            completion_pct=entry.completion_pct,
        )

    @staticmethod
    def _expected_agents_for_entry(entry: MemoryEntry) -> set[str]:
        ranked: list[tuple[tuple[float, int, int, int], str]] = []
        for stat in MemoryStore._entry_agent_stats(entry):
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

    def _is_relevant_scorecard_entry(self, entry: MemoryEntry, task: str) -> bool:
        task_scope = infer_code_scope(task)
        scope_bonus = self._scope_bonus(entry, task_scope)
        entry_text = " ".join(
            [
                entry.task,
                entry.summary,
                " ".join(entry.workspace_facts),
                " ".join(str(stat.get("detail", "")) for stat in self._entry_agent_stats(entry)),
            ]
        )
        task_terms = _keywords(task)
        overlap = len(task_terms & _keywords(entry_text))
        return overlap > 0 or scope_bonus > 0

    @staticmethod
    def _observed_failure_rate(entry: MemoryEntry, agent_type: str) -> float:
        for stat in MemoryStore._entry_agent_stats(entry):
            agent = str(stat.get("agent_type", "")).strip()
            if agent != agent_type:
                continue
            failure_count = max(_coerce_int(stat.get("failure_count")) or 0, 0)
            incomplete_count = max(_coerce_int(stat.get("incomplete_count")) or 0, 0)
            total_count = max(
                _coerce_int(stat.get("total_count"))
                or failure_count + incomplete_count,
                0,
            )
            if total_count <= 0:
                return 0.0
            return round((failure_count + incomplete_count) / total_count, 2)
        return 1.0


def serialize_subtasks(tasks: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        status = getattr(task, "status", "")
        result.append(
            {
                "name": getattr(task, "name", ""),
                "agent_type": getattr(task, "agent_type", ""),
                "prompt": getattr(task, "prompt", ""),
                "status": getattr(status, "value", str(status)),
                "pane_id": getattr(task, "pane_id", None),
            }
        )
    return result


def infer_code_scope(
    task: str,
    *texts: str,
    subtasks: list[dict[str, Any]] | None = None,
) -> list[str]:
    candidates = [task, *texts]
    if subtasks:
        for subtask in subtasks:
            if not isinstance(subtask, dict):
                continue
            candidates.extend(
                str(subtask.get(field, "")) for field in ("name", "prompt", "agent_type")
            )

    scope: list[str] = []
    for text in candidates:
        if not text:
            continue
        scope.extend(_extract_path_tokens(text))
        scope.extend(_extract_keyword_scope(text))

    return _dedupe(scope)[:12]


def _keywords(text: str) -> set[str]:
    tokens = {
        token for token in re.findall(r"[a-z0-9_]{3,}", text.lower()) if token not in _STOP_WORDS
    }
    return tokens


def _extract_path_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in re.findall(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+", text):
        normalized = match.strip("./").lower()
        if not normalized:
            continue
        parts = [part for part in re.split(r"[/.]", normalized) if part]
        if parts:
            tokens.append(parts[-1])
            if len(parts) >= 2:
                tokens.append(parts[-2])
    return tokens


def _extract_keyword_scope(text: str) -> list[str]:
    tokens = []
    for token in _keywords(text):
        if token in {"tests", "routes", "components", "frontend", "backend", "api", "docs"}:
            tokens.append(token)
        elif "/" in token:
            tokens.append(token.replace("/", "_"))
        else:
            tokens.append(token)
    return tokens


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _coerce_signal_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_stat_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _derive_workspace_facts(
    *,
    task: str,
    anchor_summaries: list[str],
    subtasks: list[dict[str, Any]],
) -> list[str]:
    facts = list(anchor_summaries)
    scope = infer_code_scope(task, *anchor_summaries, subtasks=subtasks)
    if scope:
        facts.append("Relevant scope: " + ", ".join(scope[:4]))
    return _dedupe(facts)[:4]


def _derive_performance_signals(
    *,
    subtasks: list[dict[str, Any]],
    completion_pct: int,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for task_info in subtasks[:12]:
        if not isinstance(task_info, dict):
            continue
        agent = str(task_info.get("agent_type", "")).strip()
        status = str(task_info.get("status", "")).strip().lower()
        task_name = str(task_info.get("name", "")).strip()
        prompt = str(task_info.get("prompt", "")).strip()
        if not agent and not status:
            continue
        if status == "done":
            outcome = "positive"
            verb = "completed"
        elif status in {"error", "failed"}:
            outcome = "negative"
            verb = "failed on"
        else:
            outcome = "neutral"
            verb = "worked on"

        detail = f"{agent} {verb} '{task_name or 'unknown'}'"
        if completion_pct < 100 and status == "done":
            detail += f" during a {completion_pct}% run"
        signals.append(
            {
                "agent_type": agent,
                "status": status,
                "task_name": task_name,
                "prompt": prompt,
                "outcome": outcome,
                "detail": detail,
            }
        )
    return signals[:8]


def _derive_agent_stats(
    *,
    subtasks: list[dict[str, Any]],
    completion_pct: int,
) -> list[dict[str, Any]]:
    performance_signals = _derive_performance_signals(
        subtasks=subtasks,
        completion_pct=completion_pct,
    )
    return _aggregate_agent_stats(
        performance_signals=performance_signals,
        completion_pct=completion_pct,
    )


def _aggregate_agent_stats(
    *,
    performance_signals: list[dict[str, Any]],
    completion_pct: int | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for signal in performance_signals:
        agent = str(signal.get("agent_type", "")).strip()
        if not agent:
            continue
        status = str(signal.get("status", "")).strip().lower()
        record = grouped.setdefault(
            agent,
            {
                "agent_type": agent,
                "success_count": 0,
                "failure_count": 0,
                "incomplete_count": 0,
                "total_count": 0,
                "success_rate": 0.0,
                "completion_pct": completion_pct,
                "detail": "",
            },
        )
        record["total_count"] += 1
        if status == "done":
            record["success_count"] += 1
        elif status in {"error", "failed"}:
            record["failure_count"] += 1
        else:
            record["incomplete_count"] += 1

    results: list[dict[str, Any]] = []
    for agent, record in grouped.items():
        total_count = max(int(record["total_count"]), 1)
        success_count = int(record["success_count"])
        failure_count = int(record["failure_count"])
        record["success_rate"] = round(success_count / total_count, 2)
        if failure_count:
            record["detail"] = (
                f"{agent} failed on {failure_count} of {total_count} similar subtasks"
            )
        else:
            record["detail"] = (
                f"{agent} succeeded on {success_count} of {total_count} similar subtasks"
            )
        results.append(record)
    return results
