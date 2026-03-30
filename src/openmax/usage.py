"""Per-session usage tracking: cost, tokens, duration, turns."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmax._paths import default_sessions_dir, utc_now_iso
from openmax.session_runtime import SessionStore


@dataclass
class SessionUsage:
    """Usage snapshot for a single lead-agent session."""

    session_id: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    duration_api_ms: int = 0
    num_turns: int = 0
    subtask_usage: list[dict[str, Any]] = field(default_factory=list)
    total_session_cost_usd: float = 0.0
    recorded_at: str = field(default_factory=utc_now_iso)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def subtask_total_tokens(self) -> int:
        return sum(s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in self.subtask_usage)

    def format_cost(self) -> str:
        return f"${self.cost_usd:.4f}"

    def format_duration(self) -> str:
        secs = self.duration_ms / 1000
        if secs < 60:
            return f"{secs:.1f}s"
        mins = int(secs // 60)
        remainder = secs % 60
        return f"{mins}m {remainder:.0f}s"

    def format_tokens(self) -> str:
        parts = [f"{self.input_tokens:,} in", f"{self.output_tokens:,} out"]
        if self.cache_read_tokens:
            parts.append(f"{self.cache_read_tokens:,} cache-read")
        if self.cache_creation_tokens:
            parts.append(f"{self.cache_creation_tokens:,} cache-write")
        return " / ".join(parts)

    def summary_line(self) -> str:
        return (
            f"Cost: {self.format_cost()} | "
            f"Tokens: {self.total_tokens:,} ({self.format_tokens()}) | "
            f"Duration: {self.format_duration()} | "
            f"Turns: {self.num_turns}"
        )

    def compact_line(self) -> str:
        total_cost = self.total_session_cost_usd or self.cost_usd
        return f"${total_cost:.4f}"

    def session_total_line(self) -> str:
        if not self.subtask_usage:
            return self.summary_line()
        agent_cost = sum(s.get("cost_usd", 0.0) for s in self.subtask_usage)
        total_cost = self.cost_usd + agent_cost
        return f"Total: ${total_cost:.4f} | Agents: {len(self.subtask_usage)}"


def usage_from_result(session_id: str, result_msg: object) -> SessionUsage:
    """Extract SessionUsage from a claude-agent-sdk ResultMessage."""
    cost = getattr(result_msg, "total_cost_usd", None) or 0.0
    duration_ms = getattr(result_msg, "duration_ms", 0) or 0
    duration_api_ms = getattr(result_msg, "duration_api_ms", 0) or 0
    num_turns = getattr(result_msg, "num_turns", 0) or 0
    usage_dict: dict = getattr(result_msg, "usage", None) or {}

    return SessionUsage(
        session_id=session_id,
        cost_usd=cost,
        input_tokens=usage_dict.get("input_tokens") or 0,
        output_tokens=usage_dict.get("output_tokens") or 0,
        cache_read_tokens=usage_dict.get("cache_read_input_tokens") or 0,
        cache_creation_tokens=usage_dict.get("cache_creation_input_tokens") or 0,
        duration_ms=duration_ms,
        duration_api_ms=duration_api_ms,
        num_turns=num_turns,
    )


def _load_usage_from_dict(data: dict[str, Any]) -> SessionUsage:
    """Load SessionUsage from a dict, handling missing fields for backward compat."""
    data.setdefault("subtask_usage", [])
    data.setdefault("total_session_cost_usd", 0.0)
    return SessionUsage(**data)


class UsageStore:
    """Read/write usage.json files alongside session metadata."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or default_sessions_dir()).expanduser()

    def save(self, usage: SessionUsage) -> None:
        path = self._usage_path(usage.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(usage), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, session_id: str) -> SessionUsage | None:
        path = self._usage_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _load_usage_from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list_all(self, *, limit: int | None = None) -> list[SessionUsage]:
        """List all stored usage records, newest first."""
        if not self.base_dir.exists():
            return []
        records: list[SessionUsage] = []
        for usage_path in self.base_dir.glob("*/usage.json"):
            try:
                data = json.loads(usage_path.read_text(encoding="utf-8"))
                records.append(_load_usage_from_dict(data))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        records.sort(key=lambda u: u.recorded_at, reverse=True)
        if limit is not None:
            return records[:limit]
        return records

    def aggregate(self, records: list[SessionUsage] | None = None) -> SessionUsage:
        """Aggregate multiple usage records into a single summary."""
        if records is None:
            records = self.list_all()
        agg = SessionUsage(session_id="__aggregate__")
        for rec in records:
            agg.cost_usd += rec.cost_usd
            agg.input_tokens += rec.input_tokens
            agg.output_tokens += rec.output_tokens
            agg.cache_read_tokens += rec.cache_read_tokens
            agg.cache_creation_tokens += rec.cache_creation_tokens
            agg.duration_ms += rec.duration_ms
            agg.duration_api_ms += rec.duration_api_ms
            agg.num_turns += rec.num_turns
            agg.total_session_cost_usd += rec.total_session_cost_usd
        return agg

    def _usage_path(self, session_id: str) -> Path:
        # Reuse SessionStore's directory hashing
        store = SessionStore(base_dir=self.base_dir)
        return store._session_dir(session_id) / "usage.json"
