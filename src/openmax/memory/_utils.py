"""Constants, serialization utilities, and coercion helpers for memory system."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from openmax._paths import utc_now_iso  # noqa: F401  (re-exported for models.py)

MemoryKind = Literal["lesson", "run_summary"]
MAX_MEMORY_ENTRIES = 100
_MAX_ENTRIES_PER_WORKSPACE = MAX_MEMORY_ENTRIES

_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "when",
    "your",
    "task",
    "build",
    "make",
    "add",
    "fix",
    "run",
    "use",
    "get",
    "set",
    "new",
    "code",
    "file",
    "each",
    "also",
    "was",
    "not",
    "but",
    "are",
    "has",
    "can",
    "will",
    "should",
    "been",
    "have",
    "does",
    "more",
    "all",
    "any",
    "just",
    "some",
    "only",
    "other",
    "than",
    "then",
    "very",
    "about",
    "which",
    "would",
    "could",
    "using",
    "used",
    "done",
    "doing",
    "implement",
    "update",
    "create",
    "check",
    "agent",
    "agents",
    "completed",
    "worked",
}


def default_memory_dir() -> Path:
    return Path.home() / ".openmax" / "memory"


def serialize_subtasks(tasks: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        status = getattr(task, "status", "")
        pane_id = getattr(task, "pane_id", None)
        entry: dict[str, Any] = {
            "name": getattr(task, "name", ""),
            "agent_type": getattr(task, "agent_type", ""),
            "prompt": getattr(task, "prompt", ""),
            "status": getattr(status, "value", str(status)),
            "pane_id": pane_id,
            "pane_history": [pane_id] if pane_id is not None else [],
        }
        branch_name = getattr(task, "branch_name", None)
        if branch_name:
            entry["branch_name"] = branch_name
        result.append(entry)
    return result


def _keywords(text: str, *, min_length: int = 4, filter_stopwords: bool = True) -> set[str]:
    tokens = set(re.findall(rf"[a-z0-9_]{{{min_length},}}", text.lower()))
    if filter_stopwords:
        tokens -= _STOP_WORDS
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


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
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


_MIN_RECENT_KEEP = 10


def _entry_age_and_staleness(entry: dict, now: datetime) -> tuple[float, float]:
    created = entry.get("created_at", "")
    try:
        entry_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_days = max((now - entry_time).days, 0)
    except (ValueError, TypeError):
        age_days = 365
    last_accessed = entry.get("last_accessed", 0.0)
    if isinstance(last_accessed, (int, float)) and last_accessed > 0:
        staleness_days = max((now.timestamp() - last_accessed) / 86400.0, 0)
    else:
        staleness_days = float(age_days)
    return float(age_days), staleness_days


def _confidence_bonus(entry: dict) -> float:
    confidence = entry.get("confidence")
    if isinstance(confidence, (int, float)) and confidence > 0:
        return min(confidence, 10) / 10.0
    return 0.0


def _hit_count_bonus(metadata: dict) -> float:
    hit_count = metadata.get("hit_count", 0)
    if not isinstance(hit_count, (int, float)):
        return 0.0
    return min(int(hit_count), 20) / 20.0


def _completion_bonus(entry: dict) -> float:
    completion_pct = entry.get("completion_pct")
    if isinstance(completion_pct, (int, float)) and completion_pct > 0:
        return min(completion_pct, 100) / 100.0
    return 0.0


def _eviction_score(entry: dict, now: datetime) -> float:
    age_days, staleness_days = _entry_age_and_staleness(entry, now)
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    last_matched = metadata.get("last_matched")
    no_recent_match = 1.0 if last_matched is None else 0.0
    return (
        age_days * 0.2
        + staleness_days * 0.2
        + no_recent_match * 0.5
        - _confidence_bonus(entry) * 2.0
        - _hit_count_bonus(metadata) * 2.0
        - _completion_bonus(entry) * 1.0
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
