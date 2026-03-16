"""Constants, serialization utilities, and coercion helpers for memory system."""

from __future__ import annotations

import re
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
