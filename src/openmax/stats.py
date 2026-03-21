"""Session statistics: collect, persist, and update learning metrics."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openmax._paths import utc_now_iso

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DECAY_ALPHA = 0.3
STUCK_THRESHOLD_RANGE = (2, 10)
COST_MULTIPLIER_RANGE = (0.5, 5.0)
_STATS_REL_PATH = Path(".openmax") / "stats" / "session_stats.json"


def clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


@dataclass
class SessionStats:
    schema_version: int = SCHEMA_VERSION
    updated_at: str = field(default_factory=utc_now_iso)
    sessions_count: int = 0
    avg_tokens_per_task: float = 0.0
    stuck_false_positive_rate: float = 0.0
    merge_conflict_rate_by_dir: dict[str, float] = field(default_factory=dict)
    avg_task_duration_by_type: dict[str, float] = field(default_factory=dict)
    cost_multiplier_actual_vs_estimated: float = 1.0


def _global_stats_path() -> Path:
    return Path.home() / _STATS_REL_PATH


def _project_stats_path(project_dir: str) -> Path:
    return Path(project_dir) / _STATS_REL_PATH


def _read_stats_file(path: Path) -> SessionStats | None:
    """Read and validate a stats file. Returns None on any error."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            logger.warning("Stats schema mismatch at %s, returning defaults", path)
            return None
        return SessionStats(**data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to read stats from %s: %s", path, exc)
        return None


def load_stats(project_dir: str | None = None) -> SessionStats:
    if project_dir:
        result = _read_stats_file(_project_stats_path(project_dir))
        if result:
            return result
    result = _read_stats_file(_global_stats_path())
    return result if result else SessionStats()


def _write_stats_file(stats: SessionStats, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(stats), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to write stats to %s: %s", path, exc)


def save_stats(stats: SessionStats, project_dir: str | None = None) -> None:
    _write_stats_file(stats, _global_stats_path())
    if project_dir:
        _write_stats_file(stats, _project_stats_path(project_dir))


def _ema(old: float, new: float) -> float:
    """Exponential moving average with DECAY_ALPHA."""
    return DECAY_ALPHA * new + (1 - DECAY_ALPHA) * old


def _update_dict_ema(old: dict[str, float], new: dict[str, float]) -> dict[str, float]:
    merged = dict(old)
    for key, val in new.items():
        merged[key] = _ema(merged.get(key, val), val)
    return merged


def update_stats(current: SessionStats, new_data: dict) -> SessionStats:
    updated = SessionStats(
        sessions_count=current.sessions_count + 1,
        updated_at=utc_now_iso(),
        avg_tokens_per_task=_ema(
            current.avg_tokens_per_task,
            new_data.get("avg_tokens_per_task", current.avg_tokens_per_task),
        ),
        stuck_false_positive_rate=clamp(
            _ema(
                current.stuck_false_positive_rate,
                new_data.get("stuck_false_positive_rate", current.stuck_false_positive_rate),
            ),
            *STUCK_THRESHOLD_RANGE,
        )
        if current.stuck_false_positive_rate > 0 or new_data.get("stuck_false_positive_rate", 0) > 0
        else 0.0,
        merge_conflict_rate_by_dir=_update_dict_ema(
            current.merge_conflict_rate_by_dir,
            new_data.get("merge_conflict_rate_by_dir", {}),
        ),
        avg_task_duration_by_type=_update_dict_ema(
            current.avg_task_duration_by_type,
            new_data.get("avg_task_duration_by_type", {}),
        ),
        cost_multiplier_actual_vs_estimated=clamp(
            _ema(
                current.cost_multiplier_actual_vs_estimated,
                new_data.get(
                    "cost_multiplier_actual_vs_estimated",
                    current.cost_multiplier_actual_vs_estimated,
                ),
            ),
            *COST_MULTIPLIER_RANGE,
        ),
    )
    return updated
