"""MemoryStore — core persistence, context building, and thin delegation."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openmax.memory._utils import (
    _MAX_ENTRIES_PER_WORKSPACE,
    _MIN_RECENT_KEEP,
    _dedupe,
    _derive_agent_stats,
    _derive_performance_signals,
    _derive_workspace_facts,
    _eviction_score,
    _keywords,
    default_memory_dir,
    infer_code_scope,
    utc_now_iso,
)
from openmax.memory.models import (
    AgentRecommendation,
    AgentScorecard,
    MemoryContext,
    MemoryEntry,
    RecommendationOfflineEvalReport,
    StrategyAdvice,
)
from openmax.memory.rankings import (
    _entry_agent_stats,
    _entry_facts,
    _entry_performance_signals,
    _scope_bonus,
)
from openmax.memory.rankings import (
    derive_agent_rankings as _derive_agent_rankings,
)
from openmax.memory.rankings import (
    derive_agent_scorecard as _derive_agent_scorecard,
)
from openmax.memory.rankings import (
    derive_strategy as _derive_strategy,
)
from openmax.memory.rankings import (
    evaluate_recommendations_against_baseline as _eval_against_baseline,
)
from openmax.memory.rankings import (
    evaluate_recommendations_offline as _eval_offline,
)
from openmax.memory.taxonomy import classify_task, predict_next_queries


class MemoryStore:
    """Store and retrieve workspace-scoped memory entries.

    Implements predictive memory: session-end prediction, query-distribution
    weighting, and dual-buffer context assembly.
    """

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
        category = classify_task(task)
        entry = MemoryEntry(
            memory_id=uuid.uuid4().hex,
            created_at=utc_now_iso(),
            kind="lesson",
            task=task,
            summary=lesson.strip(),
            insights=insights,
            lessons=[lesson.strip()],
            confidence=confidence,
            last_accessed=time.time(),
            source=source,
            metadata={
                "code_scope": infer_code_scope(task, lesson, rationale),
                "task_category": category,
            },
        )
        self._append_entry(cwd, entry)
        self._update_query_distribution(cwd, category)
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

        # Session-end prediction: predict likely follow-up queries
        predictions = predict_next_queries(task, completion_pct, subtasks)
        category = classify_task(task)

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
            last_accessed=time.time(),
            source="report_completion",
            metadata={
                "subtasks": subtasks[:12],
                "code_scope": infer_code_scope(
                    task,
                    notes,
                    *anchor_summaries,
                    subtasks=subtasks,
                ),
                "predictions": predictions,
                "task_category": category,
            },
        )
        self._append_entry(cwd, entry)

        # Update query distribution for this workspace
        self._update_query_distribution(cwd, category)

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

    def pin_entry(self, cwd: str, memory_id: str) -> bool:
        """Pin an entry so it is never evicted. Returns True if found."""
        return self._set_entry_field(cwd, memory_id, "pinned", True)

    def unpin_entry(self, cwd: str, memory_id: str) -> bool:
        """Unpin an entry. Returns True if found."""
        return self._set_entry_field(cwd, memory_id, "pinned", False)

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
                line = f"- {item.agent_type}: {item.success_count}/{item.total_count} succeeded"
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
            for fact in _entry_facts(entry)[:1]:
                lines.append(f"  fact: {fact}")
            for signal in _entry_performance_signals(entry)[:1]:
                detail = str(signal.get("detail", "")).strip()
                if detail:
                    lines.append(f"  signal: {detail}")
        return lines

    def build_context(self, *, cwd: str, task: str, limit: int = 4) -> MemoryContext | None:
        """Dual-buffer context assembly.

        * **Active buffer** — entries scored by direct keyword overlap with *task*.
        * **Predictive buffer** — entries whose stored predictions match *task*,
          plus entries boosted by query-distribution weights.

        Active entries fill first; predictive entries fill the remaining budget.
        """
        entries = self.load_entries(cwd)
        if not entries:
            return None

        distribution = self.load_query_distribution(cwd)

        # ── Active buffer: keyword-matched ────────────────────────
        active_budget = max(limit * 2 // 3, 1)  # ~67 % of budget
        predictive_budget = limit - active_budget  # ~33 %

        scored = [(entry, self._score_entry(entry, task)) for entry in entries]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        active_entries = [e for e, s in scored if s > 0][:active_budget]

        # ── Predictive buffer: predictions + distribution ─────────
        active_ids = {e.memory_id for e in active_entries}
        remaining = [e for e in entries if e.memory_id not in active_ids]

        predictive_scored = [
            (entry, self._predictive_score(entry, task, distribution)) for entry in remaining
        ]
        predictive_scored.sort(key=lambda pair: pair[1], reverse=True)
        predictive_entries = [e for e, s in predictive_scored if s > 0][:predictive_budget]

        # If nothing matches, return None instead of injecting irrelevant context
        selected = active_entries + predictive_entries
        if not selected:
            return None

        # ── Bump last_accessed for matched entries ────────────────
        if selected:
            self._bump_last_accessed(cwd, {e.memory_id for e in selected})

        # ── Collect matched predictions for transparency ──────────
        predictions_used: list[str] = []
        for entry in predictive_entries:
            preds = entry.metadata.get("predictions", [])
            if isinstance(preds, list):
                predictions_used.extend(str(p) for p in preds[:2])

        # ── Format output (concise — only matched entries) ─────────
        lines: list[str] = []

        # Active entries (keyword-matched)
        for entry in active_entries:
            lines.append(self._format_entry_line(entry))
            for insight in entry.insights[:1]:
                lines.append(f"  {insight}")

        # Predictive entries
        for entry in predictive_entries:
            lines.append(self._format_entry_line(entry))

        return MemoryContext(
            text="\n".join(lines),
            matched_entries=len(selected),
            active_entries=len(active_entries),
            predictive_entries=len(predictive_entries),
            predictions_used=_dedupe(predictions_used)[:4],
        )

    # ── Thin delegation to rankings module ────────────────────────

    def derive_agent_scorecard(
        self,
        *,
        cwd: str,
        task: str,
        entries: list[MemoryEntry] | None = None,
        limit: int = 4,
    ) -> list[AgentScorecard]:
        records = list(entries if entries is not None else self.load_entries(cwd))
        return _derive_agent_scorecard(
            records,
            task=task,
            score_entry=self._score_entry,
            recency_bonus=self._recency_bonus,
            limit=limit,
        )

    def derive_strategy(
        self,
        *,
        cwd: str,
        task: str,
        entries: list[MemoryEntry] | None = None,
    ) -> StrategyAdvice:
        records = list(entries if entries is not None else self.load_entries(cwd))
        return _derive_strategy(
            records,
            cwd=cwd,
            task=task,
            score_entry=self._score_entry,
            recency_bonus=self._recency_bonus,
            load_entries=self.load_entries,
        )

    def evaluate_recommendations_offline(self, *, cwd: str) -> RecommendationOfflineEvalReport:
        records = self.load_entries(cwd)
        return _eval_offline(
            records,
            label="strategy",
            history_selector=lambda run_entries, index, entry: [
                candidate
                for candidate in run_entries[:index]
                if self._is_relevant_scorecard_entry(candidate, entry.task)
            ],
            predictor=lambda history, task: self._top_scorecard_agent(
                cwd=cwd,
                task=task,
                history=history,
            ),
        )

    def evaluate_recommendations_against_baseline(
        self,
        *,
        cwd: str,
    ) -> RecommendationOfflineEvalReport:
        records = self.load_entries(cwd)
        return _eval_against_baseline(
            records,
            cwd=cwd,
            is_relevant=self._is_relevant_scorecard_entry,
            top_scorecard_agent=lambda cwd_, task, history: self._top_scorecard_agent(
                cwd=cwd_, task=task, history=history
            ),
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
        return _derive_agent_rankings(
            records,
            task=task,
            score_entry=self._score_entry,
            recency_bonus=self._recency_bonus,
            limit=limit,
        )

    # ── Private methods ───────────────────────────────────────────

    @staticmethod
    def _format_entry_line(entry: MemoryEntry) -> str:
        if entry.kind == "lesson":
            category = entry.metadata.get("task_category", "")
            prefix = f"lesson:{category}" if category else "lesson"
        else:
            prefix = "run"
        pin_marker = " [pinned]" if entry.pinned else ""
        detail = f"- [{prefix}]{pin_marker} {entry.summary}"
        if entry.confidence is not None:
            detail += f" (confidence {entry.confidence}/10)"
        if entry.completion_pct is not None:
            detail += f" [{entry.completion_pct}%]"
        return detail

    def _set_entry_field(self, cwd: str, memory_id: str, field: str, value: Any) -> bool:
        """Set a field on a raw entry dict. Returns True if entry was found."""
        payload = self._load_workspace_payload(cwd)
        for entry in payload.get("entries", []):
            if entry.get("memory_id") == memory_id:
                entry[field] = value
                payload["updated_at"] = utc_now_iso()
                path = self._workspace_path(cwd)
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
        return False

    def _append_entry(self, cwd: str, entry: MemoryEntry) -> None:
        payload = self._load_workspace_payload(cwd)
        entries = payload.setdefault("entries", [])
        entries.append(asdict(entry))
        payload["cwd"] = str(Path(cwd).resolve())
        payload["updated_at"] = utc_now_iso()
        if len(entries) > _MAX_ENTRIES_PER_WORKSPACE:
            now = datetime.now(timezone.utc)
            # Protect the N most recent entries regardless of score
            recent_ids = {e.get("memory_id") for e in entries[-_MIN_RECENT_KEEP:]}
            # Also protect the most recent run_summary entries
            run_summaries = [e for e in entries if e.get("kind") == "run_summary"]
            protected_ids = recent_ids | {
                e.get("memory_id") for e in run_summaries[-_MAX_ENTRIES_PER_WORKSPACE:]
            }
            # Pinned entries are never evicted
            pinned_ids = {e.get("memory_id") for e in entries if e.get("pinned")}
            protected_ids |= pinned_ids
            evictable = [e for e in entries if e.get("memory_id") not in protected_ids]
            evictable.sort(key=lambda e: _eviction_score(e, now), reverse=True)
            evict_count = len(entries) - _MAX_ENTRIES_PER_WORKSPACE
            evict_ids = {e.get("memory_id") for e in evictable[:evict_count]}
            payload["entries"] = [e for e in entries if e.get("memory_id") not in evict_ids]

        path = self._workspace_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _bump_last_accessed(self, cwd: str, memory_ids: set[str]) -> None:
        """Update last_accessed, hit_count, and last_matched for matched entries."""
        payload = self._load_workspace_payload(cwd)
        entries = payload.get("entries", [])
        now = time.time()
        now_iso = utc_now_iso()
        changed = False
        for entry in entries:
            if entry.get("memory_id") in memory_ids:
                entry["last_accessed"] = now
                meta = entry.setdefault("metadata", {})
                meta["hit_count"] = meta.get("hit_count", 0) + 1
                meta["last_matched"] = now_iso
                changed = True
        if changed:
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

    # ── Query distribution tracking ───────────────────────────────

    def _distribution_path(self, cwd: str) -> Path:
        resolved = str(Path(cwd).resolve())
        digest = hashlib.md5(resolved.encode(), usedforsecurity=False).hexdigest()[:16]
        return self.base_dir / f"distribution_{digest}.json"

    def _update_query_distribution(self, cwd: str, category: str) -> None:
        """Increment the count for *category* and recompute the distribution."""
        path = self._distribution_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}

        counts: dict[str, int] = data.get("counts", {})
        if not isinstance(counts, dict):
            counts = {}
        counts[category] = counts.get(category, 0) + 1
        data["counts"] = counts
        data["updated_at"] = utc_now_iso()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_query_distribution(self, cwd: str) -> dict[str, float]:
        """Return normalised category -> probability distribution for *cwd*."""
        path = self._distribution_path(cwd)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        counts: dict[str, int] = data.get("counts", {})
        if not isinstance(counts, dict):
            return {}
        total = max(sum(counts.values()), 1)
        return {cat: cnt / total for cat, cnt in counts.items()}

    # ── Predictive scoring ────────────────────────────────────────

    def _predictive_score(
        self,
        entry: MemoryEntry,
        task: str,
        distribution: dict[str, float],
    ) -> float:
        score = 0.0
        task_terms = _keywords(task)

        # 1. Prediction overlap — primary signal
        predictions = entry.metadata.get("predictions", [])
        if isinstance(predictions, list):
            for pred in predictions:
                pred_terms = _keywords(str(pred))
                overlap = len(task_terms & pred_terms)
                if overlap >= 2:
                    score += 3.0 + overlap
                elif overlap == 1:
                    score += 1.5

        # 2. Same-category distribution boost (independent signal)
        entry_category = entry.metadata.get("task_category", "")
        if not entry_category:
            entry_category = classify_task(entry.task)
        task_category = classify_task(task)

        if entry_category and entry_category == task_category and distribution:
            cat_weight = distribution.get(entry_category, 0.0)
            # Only boost when category is strongly represented (>30%)
            if cat_weight > 0.3:
                score += cat_weight * 2.0

        # 3. Recency tiebreaker (only when there's already a signal)
        if score > 0:
            try:
                ts = datetime.fromisoformat(entry.created_at)
                age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                recency = max(0.0, 1.0 - math.log1p(age_hours) / 10)
                score += recency
            except (ValueError, TypeError):
                pass

        return score

    def _score_entry(self, entry: MemoryEntry, task: str) -> int:
        task_terms = _keywords(task)
        if not task_terms:
            return 10 if entry.pinned else 0
        entry_text = " ".join(
            [
                entry.task,
                entry.summary,
                " ".join(entry.insights),
                " ".join(entry.workspace_facts),
                " ".join(entry.lessons),
            ]
        )
        entry_terms = _keywords(entry_text)
        overlap = len(task_terms & entry_terms)
        if overlap == 0 and not entry.pinned:
            return 0  # No keyword match = not relevant
        score = overlap
        if entry.pinned:
            score += 10
        # Only boost lesson/completion when there's a keyword match
        if overlap > 0:
            if entry.kind == "lesson":
                score += 1
            scope = entry.metadata.get("code_scope", [])
            task_scope = infer_code_scope(task)
            if isinstance(scope, list) and task_scope:
                scope_overlap = len(set(scope) & set(task_scope))
                score += scope_overlap * 2
        return score

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

    def _top_scorecard_agent(
        self,
        *,
        cwd: str,
        task: str,
        history: list[MemoryEntry],
    ) -> str | None:
        scorecard = self.derive_agent_scorecard(
            cwd=cwd,
            task=task,
            entries=history,
            limit=1,
        )
        if not scorecard:
            return None
        return scorecard[0].agent_type

    def _is_relevant_scorecard_entry(self, entry: MemoryEntry, task: str) -> bool:
        task_scope = infer_code_scope(task)
        scope_bonus_val = _scope_bonus(entry, task_scope)
        entry_text = " ".join(
            [
                entry.task,
                entry.summary,
                " ".join(entry.workspace_facts),
                " ".join(str(stat.get("detail", "")) for stat in _entry_agent_stats(entry)),
            ]
        )
        task_terms = _keywords(task)
        overlap = len(task_terms & _keywords(entry_text))
        return overlap > 0 or scope_bonus_val > 0
