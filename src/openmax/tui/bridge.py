"""Thread-safe bridge between DashboardProtocol callers and the Textual app."""

from __future__ import annotations

import copy
import signal
import threading
import time
from dataclasses import dataclass, field

from openmax.formatting import estimate_cost_usd

_MAX_TOOL_EVENTS = 1000
_MAX_LOG_LINES = 2000


@dataclass
class SubtaskInfo:
    """Snapshot of a single subtask's state."""

    name: str
    agent: str
    pane_id: int | None
    status: str
    started_at: float | None = None
    finished_at: float | None = None
    estimated_minutes: int | None = None


@dataclass
class DashboardState:
    """Immutable-ish snapshot of all dashboard data. Protected by external lock."""

    goal: str
    phase: str = ""
    phase_pct: int | None = None
    subtasks: dict[str, SubtaskInfo] = field(default_factory=dict)
    pane_activity: dict[int, str] = field(default_factory=dict)
    tool_events: list[dict] = field(default_factory=list)
    dispatch_prompts: dict[str, str] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    monitor_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    # session metrics
    acceleration_ratio: float | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    critical_path_seconds: float | None = None
    # per-task token tracking and cost
    task_tokens: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0


class DashboardBridge:
    """Thread-safe state container. All mutations acquire the lock."""

    def __init__(self, goal: str) -> None:
        self._state = DashboardState(goal=goal)
        self._lock = threading.Lock()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    def update_phase(self, phase: str, pct: int | None = None) -> None:
        with self._lock:
            self._state.phase = phase
            self._state.phase_pct = pct
            self._version += 1

    def update_subtask(
        self,
        name: str,
        agent: str,
        pane_id: int | None,
        status: str,
        started_at: float | None = None,
        finished_at: float | None = None,
        estimated_minutes: int | None = None,
    ) -> None:
        with self._lock:
            existing = self._state.subtasks.get(name)
            mono_started = existing.started_at if existing else None
            mono_finished = existing.finished_at if existing else None
            now = time.monotonic()
            if started_at is not None and mono_started is None:
                mono_started = now
            if finished_at is not None and mono_finished is None:
                mono_finished = now
            self._state.subtasks[name] = SubtaskInfo(
                name=name,
                agent=agent,
                pane_id=pane_id,
                status=status,
                started_at=mono_started,
                finished_at=mono_finished,
                estimated_minutes=estimated_minutes,
            )
            self._version += 1

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        with self._lock:
            self._state.pane_activity[pane_id] = last_line
            self._version += 1

    def add_tool_event(self, text: str, category: str = "system") -> None:
        with self._lock:
            self._state.tool_events.append(
                {"text": text, "category": category, "ts": time.monotonic()}
            )
            if len(self._state.tool_events) > _MAX_TOOL_EVENTS:
                self._state.tool_events = self._state.tool_events[-_MAX_TOOL_EVENTS:]
            self._version += 1

    def set_session_metrics(
        self,
        *,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        acceleration_ratio: float | None = None,
        critical_path_seconds: float | None = None,
        task_tokens: dict[str, int] | None = None,
    ) -> None:
        with self._lock:
            self._state.total_input_tokens = total_input_tokens
            self._state.total_output_tokens = total_output_tokens
            self._state.acceleration_ratio = acceleration_ratio
            self._state.critical_path_seconds = critical_path_seconds
            self._state.total_cost_usd = estimate_cost_usd(total_input_tokens, total_output_tokens)
            if task_tokens is not None:
                self._state.task_tokens = task_tokens
            self._version += 1

    def add_log(self, line: str) -> None:
        with self._lock:
            self._state.log_lines.append(line)
            if len(self._state.log_lines) > _MAX_LOG_LINES:
                self._state.log_lines = self._state.log_lines[-_MAX_LOG_LINES:]
            self._version += 1

    def set_dispatch_prompt(self, name: str, prompt: str) -> None:
        with self._lock:
            self._state.dispatch_prompts[name] = prompt
            self._version += 1

    def bump_monitor_count(self) -> None:
        with self._lock:
            self._state.monitor_count += 1
            self._version += 1

    def get_snapshot(self) -> DashboardState:
        """Return a deep copy of current state for safe reading without lock."""
        with self._lock:
            return copy.deepcopy(self._state)


class TuiDashboard:
    """Implements DashboardProtocol; bridges to Textual app via thread-safe state."""

    def __init__(self, goal: str, verbose: bool = False) -> None:
        self._bridge = DashboardBridge(goal)
        self._verbose = verbose
        self._app = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        from openmax.tui.app import OpenMaxApp

        self._app = OpenMaxApp(self._bridge)
        self._thread = threading.Thread(target=self._run_app, daemon=True)
        self._thread.start()

    def _run_app(self) -> None:
        """Run Textual app with signal registration suppressed (non-main thread)."""
        original = signal.signal
        signal.signal = lambda *_a, **_kw: signal.SIG_DFL
        try:
            self._app.run()
        finally:
            signal.signal = original

    def stop(self) -> None:
        if self._app:
            try:
                self._app.call_from_thread(self._app.exit)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def mark_connected(self) -> None:
        self._bridge.update_phase("connected")

    def update_phase(self, phase: str, pct: int | None = None) -> None:
        self._bridge.update_phase(phase, pct)

    def update_subtask(
        self,
        name: str,
        agent: str,
        pane_id: int | None,
        status: str,
        started_at: float | None = None,
        finished_at: float | None = None,
        estimated_minutes: int | None = None,
    ) -> None:
        self._bridge.update_subtask(
            name,
            agent,
            pane_id,
            status,
            started_at=started_at,
            finished_at=finished_at,
            estimated_minutes=estimated_minutes,
        )

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        self._bridge.update_pane_activity(pane_id, last_line)

    def add_tool_event(self, text: str, category: str = "system") -> None:
        self._bridge.add_tool_event(text, category)

    def set_session_metrics(
        self,
        *,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        acceleration_ratio: float | None = None,
        critical_path_seconds: float | None = None,
    ) -> None:
        self._bridge.set_session_metrics(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            acceleration_ratio=acceleration_ratio,
            critical_path_seconds=critical_path_seconds,
        )

    def set_dispatch_prompt(self, name: str, prompt: str) -> None:
        self._bridge.set_dispatch_prompt(name, prompt)

    def bump_monitor_count(self) -> None:
        self._bridge.bump_monitor_count()
