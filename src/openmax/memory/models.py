"""Data models for the memory system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from openmax._paths import utc_now_iso
from openmax.memory._utils import (
    MemoryKind,
    _coerce_dict_list,
    _coerce_int,
    _coerce_string_list,
)


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
    last_accessed: float = 0.0
    pinned: bool = False
    source: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> MemoryEntry:
        kind = item.get("kind", "lesson")
        if kind not in {"lesson", "run_summary"}:
            kind = "lesson"
        metadata = item.get("metadata", {})
        last_accessed = item.get("last_accessed", 0.0)
        try:
            last_accessed = float(last_accessed)
        except (TypeError, ValueError):
            last_accessed = 0.0
        return cls(
            memory_id=str(item.get("memory_id", uuid.uuid4().hex)),
            created_at=str(item.get("created_at", utc_now_iso())),
            kind=kind,
            task=str(item.get("task", "")).strip(),
            summary=str(item.get("summary", "")).strip(),
            insights=_coerce_string_list(item.get("insights")),
            workspace_facts=_coerce_string_list(item.get("workspace_facts")),
            lessons=_coerce_string_list(item.get("lessons")),
            performance_signals=_coerce_dict_list(item.get("performance_signals")),
            agent_stats=_coerce_dict_list(item.get("agent_stats")),
            confidence=_coerce_int(item.get("confidence")),
            completion_pct=_coerce_int(item.get("completion_pct")),
            last_accessed=last_accessed,
            pinned=bool(item.get("pinned", False)),
            source=str(item.get("source", "system")).strip() or "system",
            metadata=metadata if isinstance(metadata, dict) else {},
        )


@dataclass
class MemoryContext:
    text: str
    matched_entries: int
    # Dual-buffer breakdown
    active_entries: int = 0
    predictive_entries: int = 0
    predictions_used: list[str] = field(default_factory=list)


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
    label: str = "strategy"


@dataclass
class RecommendationOfflineEvalReport:
    strategy: RecommendationOfflineEval
    baseline: RecommendationOfflineEval
    coverage_delta: float
    hit_rate_lift: float
    completion_pct_delta: float
    failure_rate_delta: float
