"""Centralized theme system for openMax CLI output.

All semantic color/style tokens live here. Import ``get_theme()`` and reference
named fields instead of hardcoding Rich style strings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    """Semantic color and style tokens for all openMax UI surfaces."""

    # ── Status badge colors (dashboard subtask dots) ─────────────
    status_running: str = "bold yellow"
    status_done: str = "dim green"
    status_error: str = "bold underline red"
    status_pending: str = "dim italic"

    # ── Row styles (dashboard subtask rows) ──────────────────────
    row_running: str = "bold"
    row_done: str = "dim strike"
    row_error: str = "bold red"
    row_pending: str = "dim italic"

    # ── Session status colors (CLI runs / inspect) ───────────────
    session_completed: str = "green"
    session_active: str = "yellow"
    session_failed: str = "red"
    session_aborted: str = "dim"

    # ── Subtask status colors (CLI inspect subtask table) ────────
    subtask_done: str = "green"
    subtask_running: str = "yellow"
    subtask_error: str = "red"
    subtask_pending: str = "dim"
    subtask_default: str = "white"

    # ── Progress bar ─────────────────────────────────────────────
    progress_complete: str = "green"
    progress_active: str = "cyan"
    progress_empty: str = "dim"
    progress_count: str = "bold"
    progress_elapsed: str = "dim"
    progress_detail: str = "dim"
    progress_eta: str = "bold yellow"
    progress_spinner: str = "cyan"

    # ── Error detail (expanded error lines) ────────────────────
    error_detail: str = "dim red"

    # ── Done banner variations ─────────────────────────────────
    banner_warn_label: str = "bold yellow"

    # ── Phase divider ────────────────────────────────────────────
    phase_rule: str = "dim cyan"

    # ── Spinner ──────────────────────────────────────────────────
    spinner_style: str = "dim cyan"
    spinner_label: str = "dim"

    # ── Dashboard panel ──────────────────────────────────────────
    panel_border_active: str = "dim"
    panel_border_done: str = "green"

    # ── Done banner ──────────────────────────────────────────────
    banner_done_label: str = "bold green"
    banner_done_elapsed: str = "dim"
    banner_done_accel: str = "bold cyan"
    banner_detail: str = "dim"

    # ── Session summary panel ────────────────────────────────────
    summary_border: str = "green"
    summary_metric_label: str = "bold cyan"

    # ── Acceleration ratio thresholds ────────────────────────────
    accel_fast: str = "bold green"
    accel_medium: str = "bold yellow"
    accel_slow: str = "red"

    # ── Simple status line ───────────────────────────────────────
    status_phase: str = "bold cyan"
    status_elapsed: str = "dim"

    # ── Subtask table columns (dashboard) ────────────────────────
    col_task_name: str = "bold"
    col_secondary: str = "dim"
    col_detail_italic: str = "dim italic"

    # ── Tree connectors (dashboard tree layout) ─────────────────
    tree_connector: str = "dim"
    tree_header: str = "bold"
    tree_summary: str = "dim"

    # ── Table headers ────────────────────────────────────────────
    header_default: str = "bold dim"
    header_breakdown: str = "dim bold"

    # ── CLI provider panel ───────────────────────────────────────
    provider_border: str = "dim cyan"
    provider_total_border: str = "dim"
    provider_error: str = "red"

    # ── Quota bar colors ─────────────────────────────────────────
    quota_danger: str = "bold red"
    quota_warning: str = "yellow"
    quota_ok: str = "green"

    # ── CLI table column styles ──────────────────────────────────
    cli_col_bold: str = "bold"
    cli_col_dim: str = "dim"
    cli_session_default: str = "white"

    # ── Status icons (shared formatting helpers) ──────────────────
    icon_done: str = "green"
    icon_completed: str = "green"
    icon_running: str = "cyan"
    icon_active: str = "yellow"
    icon_pending: str = "dim"
    icon_error: str = "red"
    icon_failed: str = "red"
    icon_partial: str = "yellow"
    icon_aborted: str = "dim"

    # ── Doctor check results ──────────────────────────────────────
    doctor_ok: str = "green"
    doctor_fail: str = "red"
    doctor_warn: str = "yellow"


DEFAULT_THEME = Theme()


def get_theme() -> Theme:
    """Return the current theme. Allows future per-session overrides."""
    return DEFAULT_THEME
