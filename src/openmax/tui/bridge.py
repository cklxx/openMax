"""Thread-safe bridge between DashboardProtocol callers and the Textual app."""

from __future__ import annotations


class TuiDashboard:
    """Implements DashboardProtocol; bridges to Textual app via thread-safe state.

    Stub — full implementation arrives in later phases.
    """

    def __init__(self, goal: str, verbose: bool = False) -> None:
        raise NotImplementedError("TUI dashboard not yet implemented")

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def mark_connected(self) -> None:
        raise NotImplementedError

    def update_phase(self, phase: str, pct: int | None = None) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def update_pane_activity(self, pane_id: int, last_line: str) -> None:
        raise NotImplementedError

    def add_tool_event(self, text: str, category: str = "system") -> None:
        raise NotImplementedError

    def set_session_metrics(
        self,
        *,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        acceleration_ratio: float | None = None,
        critical_path_seconds: float | None = None,
    ) -> None:
        raise NotImplementedError

    def set_dispatch_prompt(self, name: str, prompt: str) -> None:
        raise NotImplementedError

    def bump_monitor_count(self) -> None:
        raise NotImplementedError
