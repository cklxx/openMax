"""Session persistence and context reconstruction for lead-agent runs."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from openmax._paths import default_sessions_dir, utc_now_iso

SCHEMA_VERSION = 1
DEFAULT_CONTEXT_CHAR_BUDGET = 6_000

TaskStatusLiteral = Literal["pending", "running", "done", "error"]


def task_hash(task: str, cwd: str) -> str:
    digest = hashlib.md5(f"{cwd}\n{task}".encode(), usedforsecurity=False)
    return digest.hexdigest()


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
    branch_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass
class PhaseAnchor:
    phase: str
    summary: str
    timestamp: str
    tasks: list[SubtaskState] = field(default_factory=list)
    completion_pct: int | None = None


@dataclass
class OverheadBreakdown:
    agent_work_seconds: float
    dispatch_seconds: float
    monitor_seconds: float
    merge_seconds: float
    other_seconds: float

    def surface(self) -> str:
        total = max(self.total, 0.01)
        return " ".join(f"{label}={self._pct(val, total)}%" for label, val in self._items())

    @property
    def total(self) -> float:
        return sum(v for _, v in self._items())

    def _items(self) -> list[tuple[str, float]]:
        return [
            ("agent", self.agent_work_seconds),
            ("dispatch", self.dispatch_seconds),
            ("monitor", self.monitor_seconds),
            ("merge", self.merge_seconds),
            ("other", self.other_seconds),
        ]

    @staticmethod
    def _pct(val: float, total: float) -> int:
        return int(round(val / total * 100))


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
    total_tool_calls: int = 0
    completion_pct: int | None = None
    startup_failure_category: str | None = None
    critical_path_seconds: float | None = None
    acceleration_ratio: float | None = None
    overhead: OverheadBreakdown | None = None
    session_duration_seconds: float | None = None

    @property
    def surface_summary(self) -> str:
        parts = [
            f"status={self.status}",
            f"completion={_format_scorecard_completion(self.completion_pct)}",
            f"duration={_format_scorecard_duration(self.duration_seconds)}",
        ]
        if self.acceleration_ratio is not None:
            parts.append(self.surface_acceleration)
        return " | ".join(parts)

    @property
    def surface_acceleration(self) -> str:
        if self.acceleration_ratio is None or self.critical_path_seconds is None:
            return ""
        return (
            f"acceleration={self.acceleration_ratio:.1f}x"
            f" (critical_path={int(self.critical_path_seconds)}s)"
        )

    @property
    def surface_details(self) -> str:
        return " | ".join(
            [
                f"subtasks={self.done_subtask_count}/{self.subtask_count} done",
                f"tool_calls={self.total_tool_calls}",
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

    @property
    def completed_task_names(self) -> list[str]:
        return [t.name for t in self.subtasks if t.status == "done"]

    @property
    def has_failures(self) -> bool:
        return any(t.status == "error" for t in self.subtasks)

    @property
    def average_task_duration_seconds(self) -> float:
        durations = [
            (t.ended_at - t.started_at).total_seconds()
            for t in self.subtasks
            if t.status == "done" and t.started_at is not None and t.ended_at is not None
        ]
        return sum(durations) / len(durations) if durations else 0.0


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
        if event_type == "lead.message":
            self._prune_lead_messages(meta.session_id)
        meta.updated_at = event.timestamp
        self._write_meta(meta)
        return event

    def _prune_lead_messages(self, session_id: str) -> None:
        events = self.load_events(session_id)
        lead_events = [e for e in events if e.event_type == "lead.message"]
        if len(lead_events) <= 100:
            return
        kept_leads = set(id(e) for e in lead_events[-50:])
        pruned = [e for e in events if e.event_type != "lead.message" or id(e) in kept_leads]
        self._rewrite_events(session_id, pruned)

    def _rewrite_events(self, session_id: str, events: list[LeadEvent]) -> None:
        events_path = self._events_path(session_id)
        lines = [json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in events]
        events_path.write_text("".join(lines), encoding="utf-8")

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
                f"Skipped {malformed_line_count} malformed event {noun}"
                " while loading session history."
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


# ---------------------------------------------------------------------------
# Event-dispatch reconstruction
# ---------------------------------------------------------------------------


@dataclass
class _ReconstructionState:
    tasks: dict[str, SubtaskState] = field(default_factory=dict)
    anchors: list[PhaseAnchor] = field(default_factory=list)
    recent_activity: list[str] = field(default_factory=list)
    latest_phase: str | None = None
    completion_pct: int | None = None
    report_notes: str | None = None
    outcome_summary: str | None = None
    manual_intervention_count: int = 0
    startup_failure_category: str | None = None


def _on_phase_anchor(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    anchor_tasks = _task_states_from_payload(payload.get("tasks"))
    state.latest_phase = str(payload.get("phase") or state.latest_phase or "")
    summary = str(payload.get("summary", "")).strip()
    completion = _coerce_int(payload.get("completion_pct"))
    for task in anchor_tasks:
        state.tasks[task.name] = task
    state.anchors.append(
        PhaseAnchor(
            phase=str(payload.get("phase", "unknown")),
            summary=summary,
            timestamp=ts,
            completion_pct=completion,
            tasks=anchor_tasks,
        )
    )
    if completion is not None:
        state.completion_pct = completion
    phase_label = payload.get("phase", "unknown")
    state.recent_activity.append(f"Phase {phase_label}: {summary or 'no summary'}")
    if len(state.anchors) > 12:
        state.anchors = state.anchors[-12:]


def _on_dispatch_agent(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    name = str(payload.get("task_name", "")).strip()
    if not name:
        return
    pane_id = _coerce_int(payload.get("pane_id"))
    existing = state.tasks.get(name)
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
    resolved_pane_id = pane_id if pane_id is not None else (existing.pane_id if existing else None)
    state.tasks[name] = SubtaskState(
        name=name,
        agent_type=agent_type,
        prompt=prompt,
        status="running",
        pane_id=resolved_pane_id,
        pane_history=pane_history,
        started_at=_parse_timestamp(ts),
    )
    pane_suffix = _pane_suffix(state.tasks[name].pane_id)
    state.recent_activity.append(
        f"Dispatched '{name}' to {state.tasks[name].agent_type}{pane_suffix}"
    )


def _pane_suffix(pane_id: int | None) -> str:
    return f" in pane {pane_id}" if pane_id is not None else ""


def _on_submit_plan(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    subtask_count = len(payload.get("subtasks", []))
    rationale = str(payload.get("rationale", "")).strip()
    preview = rationale[:100] if rationale else "no rationale"
    state.recent_activity.append(f"Plan submitted: {subtask_count} subtasks \u2014 {preview}")


def _on_mark_task_done(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    name = str(payload.get("task_name", "")).strip()
    if not name:
        return
    task = state.tasks.get(name)
    if task is None:
        task = SubtaskState(name=name, agent_type="unknown", prompt="", status="done")
    task.status = "done"
    task.ended_at = _parse_timestamp(ts)
    state.tasks[name] = task
    state.recent_activity.append(f"Marked '{name}' done")


def _on_send_text(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    state.manual_intervention_count += 1
    text = str(payload.get("text", "")).strip()
    pane_id = _coerce_int(payload.get("pane_id"))
    if not text:
        return
    preview = text if len(text) <= 120 else text[:117] + "..."
    activity = f"Intervened in pane {pane_id}: {preview}"
    if pane_id is None:
        activity = f"Intervention: {preview}"
    state.recent_activity.append(activity)


def _on_read_pane_output(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    pane_id = _coerce_int(payload.get("pane_id"))
    if pane_id is None:
        return
    stuck = payload.get("stuck", False)
    suffix = " [STUCK \u2014 no change detected]" if stuck else ""
    state.recent_activity.append(f"Read pane {pane_id} output{suffix}")


def _on_check_conflicts(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    details = str(payload.get("details", "")).strip()
    preview = details[:80] if details else "no details"
    state.recent_activity.append(f"Checked for conflicts: {preview}")


def _on_merge_agent_branch(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    name = str(payload.get("task_name", "")).strip()
    status = str(payload.get("status", "")).strip()
    commit = str(payload.get("commit", "")).strip()
    if status == "merged":
        state.recent_activity.append(f"Merged branch for '{name}' (commit {commit[:8]})")
    elif status == "conflict":
        conflict_files = payload.get("files", [])
        state.recent_activity.append(f"Merge conflict for '{name}': {len(conflict_files)} file(s)")
    else:
        state.recent_activity.append(f"Merge attempt for '{name}': {status}")


def _on_run_verification(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    check_type = str(payload.get("check_type", "")).strip()
    status = str(payload.get("status", "")).strip()
    exit_code = _coerce_int(payload.get("exit_code"))
    duration = _coerce_int(payload.get("duration_s"))
    suffix = f" (exit={exit_code})" if exit_code is not None else ""
    time_suffix = f" in {duration}s" if duration is not None else ""
    state.recent_activity.append(f"Verification [{check_type}]: {status}{suffix}{time_suffix}")


def _on_transition_phase(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    from_p = str(payload.get("from_phase", "")).strip()
    to_p = str(payload.get("to_phase", "")).strip()
    summary = str(payload.get("gate_summary", "")).strip()
    preview = summary[:60] + "..." if len(summary) > 60 else summary
    state.recent_activity.append(f"Phase: {from_p} \u2192 {to_p}: {preview}")


def _on_report_completion(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    state.completion_pct = _coerce_int(payload.get("completion_pct"))
    notes = str(payload.get("notes", "")).strip()
    state.report_notes = notes or state.report_notes
    state.recent_activity.append(
        f"Reported completion at {state.completion_pct}%"
        if state.completion_pct is not None
        else "Reported completion"
    )


def _on_usage_tokens(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    inp = _coerce_int(payload.get("input_tokens")) or 0
    out = _coerce_int(payload.get("output_tokens")) or 0
    state.recent_activity.append(f"Tokens: +{inp} in, +{out} out")


def _on_lead_message(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    text = str(payload.get("text", "")).strip()
    if text:
        preview = " ".join(text.split())
        state.recent_activity.append(f"Lead: {preview[:140]}")


def _on_session_completed(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    state.outcome_summary = "Session completed"
    state.recent_activity.append(state.outcome_summary)


def _on_session_aborted(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    reason = str(payload.get("reason", "")).strip()
    state.outcome_summary = f"Session aborted: {reason}" if reason else "Session aborted"
    state.recent_activity.append(state.outcome_summary)


def _on_startup_failed(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    raw = str(payload.get("category", "")).strip()
    state.startup_failure_category = raw or None
    state.outcome_summary = _describe_startup_failure(payload)
    state.recent_activity.append(state.outcome_summary)


def _on_resume_mismatch(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    details = str(payload.get("details", "")).strip()
    state.recent_activity.append(
        f"Resume mismatch: {details}" if details else "Resume mismatch recorded"
    )


def _on_dispatch_failed(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    name = str(payload.get("task_name", "")).strip()
    error = str(payload.get("error", "")).strip()
    agent = str(payload.get("agent_type", "")).strip()
    label = f"'{name}'" if name else "unknown task"
    state.recent_activity.append(f"Dispatch FAILED for {label} ({agent}): {error}")


def _on_context_compacted(state: _ReconstructionState, payload: dict[str, Any], ts: str) -> None:
    summary = str(payload.get("summary", "")).strip()
    if summary:
        state.recent_activity.append(f"Compacted context: {summary}")


_HandlerFn = Callable[[_ReconstructionState, dict[str, Any], str], None]

_EVENT_HANDLERS: dict[str, _HandlerFn] = {
    "phase.anchor": _on_phase_anchor,
    "tool.dispatch_agent": _on_dispatch_agent,
    "tool.submit_plan": _on_submit_plan,
    "tool.mark_task_done": _on_mark_task_done,
    "tool.send_text_to_pane": _on_send_text,
    "tool.read_pane_output": _on_read_pane_output,
    "tool.check_conflicts": _on_check_conflicts,
    "tool.merge_agent_branch": _on_merge_agent_branch,
    "tool.run_verification": _on_run_verification,
    "tool.transition_phase": _on_transition_phase,
    "tool.report_completion": _on_report_completion,
    "usage.tokens": _on_usage_tokens,
    "lead.message": _on_lead_message,
    "session.completed": _on_session_completed,
    "session.aborted": _on_session_aborted,
    "session.startup_failed": _on_startup_failed,
    "session.resume_mismatch": _on_resume_mismatch,
    "tool.dispatch_agent.failed": _on_dispatch_failed,
    "context.compacted": _on_context_compacted,
}


def _infer_completion_pct(state: _ReconstructionState, meta: SessionMeta) -> None:
    if state.completion_pct is not None or meta.status != "completed":
        return
    total = len(state.tasks)
    done = sum(1 for t in state.tasks.values() if t.status == "done")
    if total > 0:
        state.completion_pct = int(done / total * 100)


def _finalize_plan(
    meta: SessionMeta,
    events: list[LeadEvent],
    state: _ReconstructionState,
) -> ReconstructedPlan:
    _infer_completion_pct(state, meta)
    scorecard = _build_run_scorecard(
        meta=meta,
        events=events,
        tasks=list(state.tasks.values()),
        completion_pct=state.completion_pct,
        manual_intervention_count=state.manual_intervention_count,
        startup_failure_category=state.startup_failure_category,
    )
    return ReconstructedPlan(
        goal=meta.task,
        latest_phase=state.latest_phase,
        subtasks=list(state.tasks.values()),
        anchors=state.anchors,
        recent_activity=state.recent_activity[-20:],
        scorecard=scorecard,
        completion_pct=state.completion_pct,
        report_notes=state.report_notes,
        outcome_summary=state.outcome_summary,
    )


class ContextBuilder:
    """Reconstruct workflow state and derive compact prompt context."""

    def reconstruct_plan(self, meta: SessionMeta, events: list[LeadEvent]) -> ReconstructedPlan:
        state = _ReconstructionState(latest_phase=meta.latest_phase)
        for event in events:
            handler = _EVENT_HANDLERS.get(event.event_type)
            if handler:
                handler(state, event.payload, event.timestamp)
        return _finalize_plan(meta, events, state)

    def build_prompt_context(
        self,
        snapshot: SessionSnapshot,
        *,
        max_chars: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    ) -> ContextBuildResult:
        plan = snapshot.plan
        open_tasks = [task for task in plan.subtasks if task.status != "done"]
        done_tasks = [task for task in plan.subtasks if task.status == "done"]
        anchors = plan.anchors[-3:]

        sections = [
            f"Resuming session '{snapshot.meta.session_id}'.",
            f"Goal: {snapshot.meta.task}",
            f"Phase: {plan.latest_phase or 'unknown'}",
        ]

        if anchors:
            anchor_lines = [
                f"- {anchor.phase}: {(anchor.summary or 'no summary')[:120]}" for anchor in anchors
            ]
            sections.append("Anchors:\n" + "\n".join(anchor_lines))

        if open_tasks:
            task_lines = [
                f"- {task.name} [{task.status}] via {task.agent_type}"
                + (f" pane={task.pane_id}" if task.pane_id is not None else "")
                for task in open_tasks
            ]
            sections.append("Open subtasks:\n" + "\n".join(task_lines))

        if done_tasks:
            sections.append(f"Completed: {len(done_tasks)} subtask(s)")

        if plan.recent_activity:
            key_activity = [
                item
                for item in plan.recent_activity[-20:]
                if any(
                    kw in item
                    for kw in ("error", "Error", "phase", "done", "fail", "merge", "conflict")
                )
            ][-8:]
            if key_activity:
                sections.append("Key activity:\n" + "\n".join(f"- {item}" for item in key_activity))

        if plan.report_notes:
            sections.append(f"Latest completion note: {plan.report_notes}")

        text = "\n\n".join(section for section in sections if section.strip())
        if len(text) <= max_chars:
            return ContextBuildResult(text=text)

        compact_sections = [
            f"Resuming session '{snapshot.meta.session_id}'.",
            f"Goal: {snapshot.meta.task}",
            f"Phase: {plan.latest_phase or 'unknown'}",
        ]
        if open_tasks:
            compact_sections.append(
                "Open: " + ", ".join(f"{task.name}[{task.status}]" for task in open_tasks[:8])
            )
        if done_tasks:
            compact_sections.append(f"Done: {len(done_tasks)} subtask(s)")
        if plan.report_notes:
            compact_sections.append(f"Note: {plan.report_notes[:300]}")

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
        branch = item.get("branch_name")
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
                branch_name=str(branch) if branch else None,
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


def _compute_acceleration_ratio(
    events: list[LeadEvent],
) -> tuple[float | None, float | None]:
    """Return (critical_path_seconds, acceleration_ratio) from event history."""
    deps = _extract_task_deps(events)
    durations = _extract_task_durations(events)
    if not durations:
        return None, None
    wall = _wall_clock_seconds(events)
    if wall is None or wall <= 0:
        return None, None
    cp = _critical_path(durations, deps)
    if cp <= 0:
        return None, None
    return cp, round(wall / cp, 2)


def _extract_task_deps(events: list[LeadEvent]) -> dict[str, list[str]]:
    for ev in events:
        if ev.event_type == "tool.submit_plan":
            subtasks = ev.payload.get("subtasks", [])
            return {
                str(st.get("name", "")): [str(d) for d in st.get("dependencies", [])]
                for st in subtasks
                if isinstance(st, dict)
            }
    return {}


def _extract_task_durations(events: list[LeadEvent]) -> dict[str, float]:
    dispatch_times: dict[str, datetime] = {}
    done_times: dict[str, datetime] = {}
    for ev in events:
        if ev.event_type == "tool.dispatch_agent":
            name = str(ev.payload.get("task_name", ""))
            if name and name not in dispatch_times:
                dispatch_times[name] = _parse_timestamp(ev.timestamp)
        elif ev.event_type == "tool.mark_task_done":
            name = str(ev.payload.get("task_name", ""))
            if name:
                done_times[name] = _parse_timestamp(ev.timestamp)
    result: dict[str, float] = {}
    for name, start in dispatch_times.items():
        end = done_times.get(name)
        if end is not None:
            result[name] = max((end - start).total_seconds(), 0)
    return result


def _critical_path(
    durations: dict[str, float],
    deps: dict[str, list[str]],
) -> float:
    cache: dict[str, float] = {}

    def cp(task: str) -> float:
        if task in cache:
            return cache[task]
        dur = durations.get(task, 0)
        dep_max = max((cp(d) for d in deps.get(task, []) if d in durations), default=0)
        cache[task] = dur + dep_max
        return cache[task]

    return max((cp(t) for t in durations), default=0)


def _wall_clock_seconds(events: list[LeadEvent]) -> float | None:
    dispatch_times = [
        _parse_timestamp(ev.timestamp) for ev in events if ev.event_type == "tool.dispatch_agent"
    ]
    done_times = [
        _parse_timestamp(ev.timestamp) for ev in events if ev.event_type == "tool.mark_task_done"
    ]
    if not dispatch_times or not done_times:
        return None
    return (max(done_times) - min(dispatch_times)).total_seconds()


def _compute_overhead_breakdown(
    events: list[LeadEvent],
    total_seconds: float | None,
) -> OverheadBreakdown | None:
    if not events or not total_seconds or total_seconds <= 0:
        return None
    durations = _extract_task_durations(events)
    agent_work = sum(durations.values())
    dispatch_s = 0.0
    monitor_s = 0.0
    merge_s = 0.0
    _DISPATCH = {"tool.dispatch_agent", "tool.dispatch_agent.failed"}
    _MONITOR = {"tool.read_pane_output", "tool.check_conflicts"}
    _MERGE = {"tool.merge_agent_branch", "tool.run_verification"}
    sorted_events = sorted(events, key=lambda e: e.timestamp)
    for i in range(1, len(sorted_events)):
        gap = (
            _parse_timestamp(sorted_events[i].timestamp)
            - _parse_timestamp(sorted_events[i - 1].timestamp)
        ).total_seconds()
        if gap <= 0:
            continue
        etype = sorted_events[i].event_type
        if etype in _DISPATCH:
            dispatch_s += gap
        elif etype in _MONITOR:
            monitor_s += gap
        elif etype in _MERGE:
            merge_s += gap
    non_agent = dispatch_s + monitor_s + merge_s
    other = max(total_seconds - agent_work - non_agent, 0)
    return OverheadBreakdown(
        agent_work_seconds=agent_work,
        dispatch_seconds=dispatch_s,
        monitor_seconds=monitor_s,
        merge_seconds=merge_s,
        other_seconds=other,
    )


def _compute_session_duration(events: list[LeadEvent]) -> float | None:
    if not events:
        return None
    timestamps = [_parse_timestamp(ev.timestamp) for ev in events]
    return max(0.0, (max(timestamps) - min(timestamps)).total_seconds())


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

    total_tool_calls = sum(1 for ev in events if ev.event_type.startswith("tool."))
    done_subtask_count = sum(1 for task in tasks if task.status == "done")
    session_duration_seconds = _compute_session_duration(events)
    cp_seconds, accel_ratio = _compute_acceleration_ratio(events)
    overhead = _compute_overhead_breakdown(
        events,
        float(duration_seconds) if duration_seconds is not None else None,
    )
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
        total_tool_calls=total_tool_calls,
        completion_pct=completion_pct,
        startup_failure_category=startup_failure_category,
        critical_path_seconds=cp_seconds,
        acceleration_ratio=accel_ratio,
        overhead=overhead,
        session_duration_seconds=session_duration_seconds,
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
