"""Session persistence and context reconstruction for lead-agent runs."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
DEFAULT_CONTEXT_CHAR_BUDGET = 12_000

TaskStatusLiteral = Literal["pending", "running", "done", "error"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_hash(task: str, cwd: str) -> str:
    digest = hashlib.md5(f"{cwd}\n{task}".encode(), usedforsecurity=False)
    return digest.hexdigest()


def default_sessions_dir() -> Path:
    return Path.home() / ".openmax" / "sessions"


@dataclass
class SessionMeta:
    session_id: str
    task: str
    cwd: str
    task_hash: str
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    status: str = "active"
    latest_phase: str | None = None


@dataclass
class LeadEvent:
    event_id: str
    event_type: str
    session_id: str
    cwd: str
    task_hash: str
    timestamp: str
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION


@dataclass
class SubtaskState:
    name: str
    agent_type: str
    prompt: str
    status: TaskStatusLiteral
    pane_id: int | None = None
    pane_history: list[int] = field(default_factory=list)


@dataclass
class PhaseAnchor:
    phase: str
    summary: str
    timestamp: str
    tasks: list[SubtaskState] = field(default_factory=list)
    completion_pct: int | None = None


@dataclass
class RunScorecard:
    status: str
    success: bool
    failure: bool
    duration_seconds: int | None
    subtask_count: int
    done_subtask_count: int
    manual_intervention_count: int
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    completion_pct: int | None = None
    startup_failure_category: str | None = None

    @property
    def surface_summary(self) -> str:
        return " | ".join(
            [
                f"status={self.status}",
                f"completion={_format_scorecard_completion(self.completion_pct)}",
                f"duration={_format_scorecard_duration(self.duration_seconds)}",
            ]
        )

    @property
    def surface_details(self) -> str:
        return " | ".join(
            [
                f"subtasks={self.done_subtask_count}/{self.subtask_count} done",
                f"interventions={self.manual_intervention_count}",
                "startup_failure=" + (self.startup_failure_category or "n/a"),
            ]
        )


@dataclass
class ReconstructedPlan:
    goal: str
    latest_phase: str | None
    subtasks: list[SubtaskState]
    anchors: list[PhaseAnchor]
    recent_activity: list[str]
    scorecard: RunScorecard
    completion_pct: int | None = None
    report_notes: str | None = None
    outcome_summary: str | None = None


@dataclass
class SessionSnapshot:
    meta: SessionMeta
    events: list[LeadEvent]
    plan: ReconstructedPlan
    load_warnings: list[str] = field(default_factory=list)


@dataclass
class ContextBuildResult:
    text: str
    compaction_summary: str | None = None


@dataclass
class LeadAgentRuntime:
    """Mutable runtime state for a single lead-agent session."""

    cwd: str
    plan: Any
    pane_mgr: Any
    agent_window_id: int | None = None
    session_store: SessionStore | None = None
    session_meta: SessionMeta | None = None
    memory_store: Any | None = None
    allowed_agents: list[str] | None = None
    agent_registry: Any | None = None
    dashboard: Any | None = None
    pane_output_hashes: dict[int, list[str]] = field(default_factory=dict)
    plan_submitted: bool = False


_lead_agent_runtime: ContextVar[LeadAgentRuntime | None] = ContextVar(
    "openmax_lead_agent_runtime",
    default=None,
)


def bind_lead_agent_runtime(runtime: LeadAgentRuntime) -> Token[LeadAgentRuntime | None]:
    return _lead_agent_runtime.set(runtime)


def reset_lead_agent_runtime(token: Token[LeadAgentRuntime | None]) -> None:
    _lead_agent_runtime.reset(token)


def get_lead_agent_runtime() -> LeadAgentRuntime:
    runtime = _lead_agent_runtime.get()
    if runtime is None:
        raise RuntimeError("Lead agent runtime is not initialized")
    return runtime


class SessionStore:
    """Append-only JSONL event store plus small session metadata."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or default_sessions_dir()).expanduser()

    def create_session(self, session_id: str, task: str, cwd: str) -> SessionMeta:
        path = self._session_dir(session_id)
        if path.exists():
            raise RuntimeError(
                f"Session '{session_id}' already exists. Use --resume to continue it."
            )
        path.mkdir(parents=True, exist_ok=True)
        meta = SessionMeta(
            session_id=session_id,
            task=task,
            cwd=str(Path(cwd).resolve()),
            task_hash=task_hash(task, str(Path(cwd).resolve())),
        )
        self._write_meta(meta)
        return meta

    def load_meta(self, session_id: str) -> SessionMeta:
        meta_path = self._meta_path(session_id)
        if not meta_path.exists():
            raise RuntimeError(f"Session '{session_id}' was not found.")
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return SessionMeta(**data)

    def save_meta(self, meta: SessionMeta) -> None:
        meta.updated_at = utc_now_iso()
        self._write_meta(meta)

    def append_event(
        self,
        meta: SessionMeta,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> LeadEvent:
        event = LeadEvent(
            event_id=uuid.uuid4().hex,
            event_type=event_type,
            session_id=meta.session_id,
            cwd=meta.cwd,
            task_hash=meta.task_hash,
            timestamp=utc_now_iso(),
            payload=payload or {},
        )
        events_path = self._events_path(meta.session_id)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        meta.updated_at = event.timestamp
        self._write_meta(meta)
        return event

    def load_events(self, session_id: str) -> list[LeadEvent]:
        events, _warnings = self._load_events_with_warnings(session_id)
        return events

    def _load_events_with_warnings(self, session_id: str) -> tuple[list[LeadEvent], list[str]]:
        events_path = self._events_path(session_id)
        if not events_path.exists():
            return [], []
        events: list[LeadEvent] = []
        malformed_line_count = 0
        with events_path.open(encoding="utf-8") as file_obj:
            for line in file_obj:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    events.append(LeadEvent(**data))
                except (json.JSONDecodeError, TypeError, ValueError):
                    malformed_line_count += 1

        warnings: list[str] = []
        if malformed_line_count:
            noun = "line" if malformed_line_count == 1 else "lines"
            warnings.append(
                f"Skipped {malformed_line_count} malformed event {noun} while loading "
                "session history."
            )
        return events, warnings

    def load_snapshot(self, session_id: str) -> SessionSnapshot:
        meta = self.load_meta(session_id)
        events, load_warnings = self._load_events_with_warnings(session_id)
        plan = ContextBuilder().reconstruct_plan(meta, events)
        return SessionSnapshot(
            meta=meta,
            events=events,
            plan=plan,
            load_warnings=load_warnings,
        )

    def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[SessionMeta]:
        if not self.base_dir.exists():
            return []

        sessions: list[SessionMeta] = []
        for meta_path in self.base_dir.glob("*/meta.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                sessions.append(SessionMeta(**data))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue

        sessions.sort(
            key=lambda meta: (
                _parse_timestamp(meta.updated_at),
                _parse_timestamp(meta.created_at),
                meta.session_id,
            ),
            reverse=True,
        )
        if status is not None:
            sessions = [meta for meta in sessions if meta.status == status]
        if limit is not None:
            return sessions[:limit]
        return sessions

    def session_exists(self, session_id: str) -> bool:
        return self._meta_path(session_id).exists()

    def find_active_session(self, task_hash_value: str) -> SessionMeta | None:
        """Return the most recent non-completed session matching task_hash, or None."""
        candidates = self.list_sessions()
        for meta in candidates:
            if meta.task_hash == task_hash_value and meta.status not in (
                "completed",
                "aborted",
                "failed",
            ):
                return meta
        return None

    def _write_meta(self, meta: SessionMeta) -> None:
        meta_path = self._meta_path(meta.session_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _session_dir(self, session_id: str) -> Path:
        safe = hashlib.md5(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        return self.base_dir / safe

    def _meta_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "meta.json"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"


def reconcile_resumed_subtasks(
    plan: ReconstructedPlan,
    pane_mgr: Any,
) -> list[str]:
    """Reset running subtasks whose panes are gone. Returns list of reset task names."""
    reset_names: list[str] = []
    for subtask in plan.subtasks:
        if subtask.status == "running":
            if subtask.pane_id is None or not pane_mgr.is_pane_alive(subtask.pane_id):
                subtask.status = "pending"
                reset_names.append(subtask.name)
    return reset_names


class ContextBuilder:
    """Reconstruct workflow state and derive compact prompt context."""

    def reconstruct_plan(self, meta: SessionMeta, events: list[LeadEvent]) -> ReconstructedPlan:
        tasks: dict[str, SubtaskState] = {}
        anchors: list[PhaseAnchor] = []
        recent_activity: list[str] = []
        latest_phase = meta.latest_phase
        completion_pct: int | None = None
        report_notes: str | None = None
        outcome_summary: str | None = None
        manual_intervention_count = 0
        startup_failure_category: str | None = None

        for event in events:
            event_type = event.event_type
            payload = event.payload

            if event_type == "phase.anchor":
                anchor_tasks = _task_states_from_payload(payload.get("tasks"))
                latest_phase = str(payload.get("phase") or latest_phase or "")
                anchor_summary = str(payload.get("summary", "")).strip()
                anchor_completion = _coerce_int(payload.get("completion_pct"))
                for task in anchor_tasks:
                    tasks[task.name] = task
                anchors.append(
                    PhaseAnchor(
                        phase=str(payload.get("phase", "unknown")),
                        summary=anchor_summary,
                        timestamp=event.timestamp,
                        completion_pct=anchor_completion,
                        tasks=anchor_tasks,
                    )
                )
                if anchor_completion is not None:
                    completion_pct = anchor_completion
                recent_activity.append(
                    f"Phase {payload.get('phase', 'unknown')}: {anchor_summary or 'no summary'}"
                )
                if len(anchors) > 12:
                    anchors = anchors[-12:]
                continue

            if event_type == "tool.dispatch_agent":
                name = str(payload.get("task_name", "")).strip()
                if name:
                    pane_id = _coerce_int(payload.get("pane_id"))
                    existing = tasks.get(name)
                    pane_history = list(existing.pane_history) if existing else []
                    if pane_id is not None and pane_id not in pane_history:
                        pane_history.append(pane_id)
                    agent_type = str(
                        payload.get(
                            "agent_type",
                            existing.agent_type if existing else "generic",
                        )
                    )
                    prompt = str(payload.get("prompt", existing.prompt if existing else ""))
                    resolved_pane_id = (
                        pane_id if pane_id is not None else (existing.pane_id if existing else None)
                    )
                    tasks[name] = SubtaskState(
                        name=name,
                        agent_type=agent_type,
                        prompt=prompt,
                        status="running",
                        pane_id=resolved_pane_id,
                        pane_history=pane_history,
                    )
                    pane_suffix = (
                        f" in pane {tasks[name].pane_id}" if tasks[name].pane_id is not None else ""
                    )
                    recent_activity.append(
                        f"Dispatched '{name}' to {tasks[name].agent_type}{pane_suffix}"
                    )
                continue

            if event_type == "tool.submit_plan":
                subtask_count = len(payload.get("subtasks", []))
                rationale = str(payload.get("rationale", "")).strip()
                preview = rationale[:100] if rationale else "no rationale"
                recent_activity.append(f"Plan submitted: {subtask_count} subtasks — {preview}")
                continue

            if event_type == "tool.mark_task_done":
                name = str(payload.get("task_name", "")).strip()
                if name:
                    task = tasks.get(name)
                    if task is None:
                        task = SubtaskState(
                            name=name,
                            agent_type="unknown",
                            prompt="",
                            status="done",
                        )
                    task.status = "done"
                    tasks[name] = task
                    recent_activity.append(f"Marked '{name}' done")
                continue

            if event_type == "tool.send_text_to_pane":
                manual_intervention_count += 1
                text = str(payload.get("text", "")).strip()
                pane_id = _coerce_int(payload.get("pane_id"))
                if text:
                    preview = text if len(text) <= 120 else text[:117] + "..."
                    activity = f"Intervened in pane {pane_id}: {preview}"
                    if pane_id is None:
                        activity = f"Intervention: {preview}"
                    recent_activity.append(activity)
                continue

            if event_type == "tool.read_pane_output":
                pane_id = _coerce_int(payload.get("pane_id"))
                if pane_id is not None:
                    stuck = payload.get("stuck", False)
                    if stuck:
                        recent_activity.append(
                            f"Read pane {pane_id} output [STUCK — no change detected]"
                        )
                    else:
                        recent_activity.append(f"Read pane {pane_id} output")
                continue

            if event_type == "tool.run_verification":
                check_type = str(payload.get("check_type", "")).strip()
                status = str(payload.get("status", "")).strip()
                exit_code = _coerce_int(payload.get("exit_code"))
                duration = _coerce_int(payload.get("duration_s"))
                suffix = f" (exit={exit_code})" if exit_code is not None else ""
                time_suffix = f" in {duration}s" if duration is not None else ""
                recent_activity.append(
                    f"Verification [{check_type}]: {status}{suffix}{time_suffix}"
                )
                continue

            if event_type == "tool.report_completion":
                completion_pct = _coerce_int(payload.get("completion_pct"))
                report_notes = str(payload.get("notes", "")).strip() or report_notes
                recent_activity.append(
                    f"Reported completion at {completion_pct}%"
                    if completion_pct is not None
                    else "Reported completion"
                )
                continue

            if event_type == "usage.tokens":
                inp = _coerce_int(payload.get("input_tokens")) or 0
                out = _coerce_int(payload.get("output_tokens")) or 0
                recent_activity.append(f"Tokens: +{inp} in, +{out} out")
                continue

            if event_type == "lead.message":
                text = str(payload.get("text", "")).strip()
                if text:
                    preview = " ".join(text.split())
                    recent_activity.append(f"Lead: {preview[:140]}")
                continue

            if event_type == "session.completed":
                # Only keep an explicit completion_pct; never fabricate 100%.
                # If no report_completion was called, we infer from subtask
                # ratios later — assuming 100% hides false-complete runs.
                outcome_summary = "Session completed"
                recent_activity.append(outcome_summary)
                continue

            if event_type == "session.aborted":
                reason = str(payload.get("reason", "")).strip()
                outcome_summary = f"Session aborted: {reason}" if reason else "Session aborted"
                recent_activity.append(outcome_summary)
                continue

            if event_type == "session.startup_failed":
                startup_failure_category = str(payload.get("category", "")).strip() or None
                outcome_summary = _describe_startup_failure(payload)
                recent_activity.append(outcome_summary)
                continue

            if event_type == "session.resume_mismatch":
                details = str(payload.get("details", "")).strip()
                recent_activity.append(
                    f"Resume mismatch: {details}" if details else "Resume mismatch recorded"
                )
                continue

            if event_type == "context.compacted":
                summary = str(payload.get("summary", "")).strip()
                if summary:
                    recent_activity.append(f"Compacted context: {summary}")

        if completion_pct is None and meta.status == "completed":
            # Infer from subtask ratios instead of blindly claiming 100%.
            # This prevents false-complete reporting when the lead agent
            # finishes without calling report_completion (crash, timeout,
            # or skipped verification).
            total = len(tasks)
            done = sum(1 for t in tasks.values() if t.status == "done")
            if total > 0:
                completion_pct = int(done / total * 100)
            # If no subtasks exist either, leave as None → shows "n/a".

        scorecard = _build_run_scorecard(
            meta=meta,
            events=events,
            tasks=list(tasks.values()),
            completion_pct=completion_pct,
            manual_intervention_count=manual_intervention_count,
            startup_failure_category=startup_failure_category,
        )

        return ReconstructedPlan(
            goal=meta.task,
            latest_phase=latest_phase,
            subtasks=list(tasks.values()),
            anchors=anchors,
            recent_activity=recent_activity[-20:],
            scorecard=scorecard,
            completion_pct=completion_pct,
            report_notes=report_notes,
            outcome_summary=outcome_summary,
        )

    def build_prompt_context(
        self,
        snapshot: SessionSnapshot,
        *,
        max_chars: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    ) -> ContextBuildResult:
        plan = snapshot.plan
        open_tasks = [task for task in plan.subtasks if task.status != "done"]
        done_tasks = [task for task in plan.subtasks if task.status == "done"]
        anchors = plan.anchors[-5:]

        sections = [
            f"Resuming prior lead session '{snapshot.meta.session_id}'.",
            f"Previous goal: {snapshot.meta.task}",
            f"Latest recorded phase: {plan.latest_phase or 'unknown'}",
        ]

        if anchors:
            anchor_lines = [
                f"- {anchor.phase} @ {anchor.timestamp}: {anchor.summary or 'no summary'}"
                for anchor in anchors
            ]
            sections.append("Phase anchors:\n" + "\n".join(anchor_lines))

        if open_tasks:
            task_lines = [
                f"- **Action required** {task.name} [{task.status}]"
                f" via {task.agent_type}"
                + (f" pane={task.pane_id}" if task.pane_id is not None else "")
                for task in open_tasks
            ]
            sections.append("Open subtasks:\n" + "\n".join(task_lines))

        if done_tasks:
            sections.append(
                "Completed subtasks:\n"
                + "\n".join(f"- {task.name}" for task in done_tasks[:10])
                + (f"\n- ... and {len(done_tasks) - 10} more" if len(done_tasks) > 10 else "")
            )

        if plan.recent_activity:
            recent_lines = "\n".join(f"- {item}" for item in plan.recent_activity[-12:])
            sections.append("Recent workflow activity:\n" + recent_lines)

        if plan.report_notes:
            sections.append(f"Latest completion note: {plan.report_notes}")

        text = "\n\n".join(section for section in sections if section.strip())
        if len(text) <= max_chars:
            return ContextBuildResult(text=text)

        compact_sections = [
            f"Resuming prior lead session '{snapshot.meta.session_id}'.",
            f"Goal: {snapshot.meta.task}",
            f"Latest phase: {plan.latest_phase or 'unknown'}",
        ]
        if open_tasks:
            compact_sections.append(
                "Open subtasks:\n"
                + "\n".join(
                    f"- {task.name} [{task.status}] via {task.agent_type}"
                    for task in open_tasks[:12]
                )
            )
        if anchors:
            compact_sections.append(
                "Most recent anchors:\n"
                + "\n".join(
                    f"- {anchor.phase}: {(anchor.summary or 'no summary')[:120]}"
                    for anchor in anchors[-2:]
                )
            )
        if done_tasks:
            compact_sections.append(f"Completed subtasks count: {len(done_tasks)}")
        if plan.report_notes:
            compact_sections.append(f"Latest note: {plan.report_notes[:500]}")

        compact_text = "\n\n".join(compact_sections)
        return ContextBuildResult(
            text=compact_text[:max_chars],
            compaction_summary=(
                f"Context condensed to {len(open_tasks)} open tasks, "
                f"{len(done_tasks)} completed tasks, "
                f"latest phase '{plan.latest_phase or 'unknown'}'."
            ),
        )


def anchor_payload(
    *,
    phase: str,
    summary: str,
    tasks: list[dict[str, Any]],
    completion_pct: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": phase,
        "summary": summary,
        "tasks": tasks,
    }
    if completion_pct is not None:
        payload["completion_pct"] = completion_pct
    return payload


def serialize_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for task in tasks:
        pane_id = getattr(task, "pane_id", None)
        status = getattr(task, "status")
        status_value = getattr(status, "value", status)
        serialized.append(
            {
                "name": getattr(task, "name"),
                "agent_type": getattr(task, "agent_type"),
                "prompt": getattr(task, "prompt"),
                "status": str(status_value),
                "pane_id": pane_id,
                "pane_history": [pane_id] if pane_id is not None else [],
            }
        )
    return serialized


def _task_states_from_payload(value: Any) -> list[SubtaskState]:
    if not isinstance(value, list):
        return []
    result: list[SubtaskState] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "pending"))
        if status.startswith("TaskStatus."):
            status = status.rsplit(".", 1)[-1].lower()
        result.append(
            SubtaskState(
                name=str(item.get("name", "")),
                agent_type=str(item.get("agent_type", "generic")),
                prompt=str(item.get("prompt", "")),
                status=_normalize_status(status),
                pane_id=_coerce_int(item.get("pane_id")),
                pane_history=[
                    pane for pane in item.get("pane_history", []) if isinstance(pane, int)
                ],
            )
        )
    return result


def _normalize_status(value: str) -> TaskStatusLiteral:
    normalized = value.lower()
    if normalized in {"pending", "running", "done", "error"}:
        return normalized
    return "pending"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _describe_startup_failure(payload: dict[str, Any]) -> str:
    category = str(payload.get("category", "")).strip()
    detail = str(payload.get("detail", "")).strip()
    stage = str(payload.get("stage", "")).strip()
    description = "Lead agent startup failed"
    if category:
        description += f" [{category}]"
    if stage:
        description += f" during {stage}"
    if detail:
        description += f": {detail}"
    return description


def _build_run_scorecard(
    *,
    meta: SessionMeta,
    events: list[LeadEvent],
    tasks: list[SubtaskState],
    completion_pct: int | None,
    manual_intervention_count: int,
    startup_failure_category: str | None,
) -> RunScorecard:
    terminal_time = _resolve_terminal_timestamp(meta, events)
    created_at = _parse_timestamp(meta.created_at)
    duration_seconds: int | None = None
    if terminal_time is not None:
        duration_seconds = max(int((terminal_time - created_at).total_seconds()), 0)

    total_input_tokens = 0
    total_output_tokens = 0
    for ev in events:
        if ev.event_type == "usage.tokens":
            total_input_tokens += _coerce_int(ev.payload.get("input_tokens")) or 0
            total_output_tokens += _coerce_int(ev.payload.get("output_tokens")) or 0

    done_subtask_count = sum(1 for task in tasks if task.status == "done")
    return RunScorecard(
        status=meta.status,
        success=meta.status == "completed",
        failure=meta.status in {"failed", "aborted"},
        duration_seconds=duration_seconds,
        subtask_count=len(tasks),
        done_subtask_count=done_subtask_count,
        manual_intervention_count=manual_intervention_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        completion_pct=completion_pct,
        startup_failure_category=startup_failure_category,
    )


def _format_scorecard_completion(value: int | None) -> str:
    return f"{value}%" if value is not None else "n/a"


def _format_scorecard_duration(value: int | None) -> str:
    return f"{value}s" if value is not None else "n/a"


def _resolve_terminal_timestamp(meta: SessionMeta, events: list[LeadEvent]) -> datetime | None:
    candidates = [_parse_timestamp(meta.updated_at)]
    candidates.extend(_parse_timestamp(event.timestamp) for event in events if event.timestamp)
    if not candidates:
        return None
    return max(candidates)
