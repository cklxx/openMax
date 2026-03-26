"""Private helper functions shared across tool modules."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio

from openmax.lead_agent.runtime import LeadAgentRuntime, get_lead_agent_runtime
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.output import P, console
from openmax.pane_backend import PaneBackendError
from openmax.pane_manager import PaneManager
from openmax.session_runtime import anchor_payload
from openmax.task_file import read_report, read_shared_context, report_path

_VALID_PHASE_TRANSITIONS: dict[str, set[str]] = {
    "research": {"plan"},
    "plan": {"implement"},
    "implement": {"verify"},
    "verify": {"finish", "implement"},  # allow re-dispatch via verify → implement
}

_CHECKPOINT_PROTOCOL = """

## Checkpoint Protocol

For significant decision forks: write `.openmax/checkpoints/{task_name}.md`
with sections "Decision needed", "Options" (pros/cons), "My recommendation".
Then pause and wait for a decision. Routine choices are yours to make.
"""


def _runtime() -> LeadAgentRuntime:
    return get_lead_agent_runtime()


def _append_session_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    runtime = _runtime()
    if runtime.session_store is None or runtime.session_meta is None:
        return
    runtime.session_store.append_event(runtime.session_meta, event_type, payload)


def _update_session_phase(phase: str | None) -> None:
    runtime = _runtime()
    if runtime.session_store is None or runtime.session_meta is None or not phase:
        return
    runtime.session_meta.latest_phase = phase
    runtime.session_store.save_meta(runtime.session_meta)


def _upsert_subtask(subtask: SubTask) -> None:
    runtime = _runtime()
    if runtime.plan is None:
        raise RuntimeError("Lead agent plan is not initialized")
    for index, existing in enumerate(runtime.plan.subtasks):
        if existing.name == subtask.name:
            runtime.plan.subtasks[index] = subtask
            return
    runtime.plan.subtasks.append(subtask)


def _apply_subtask_usage(task_name: str, raw: dict[str, Any]) -> None:
    """Apply token usage data from a mailbox done message to the matching SubTask."""
    input_tok = raw.get("input_tokens", 0)
    output_tok = raw.get("output_tokens", 0)
    if not input_tok and not output_tok:
        return
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.name == task_name:
            st.input_tokens = max(int(input_tok), 0)
            st.output_tokens = max(int(output_tok), 0)
            st.tokens_used = st.input_tokens + st.output_tokens
            st.cost_usd = max(float(raw.get("cost_usd", 0.0)), 0.0)
            st.usage_source = "reported"
            return


def _serialize_subtasks(tasks: list[Any]) -> list[dict[str, Any]]:
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


def _record_phase_anchor(phase: str, summary: str, completion_pct: int | None = None) -> None:
    runtime = _runtime()
    if runtime.plan is None:
        return
    normalized_phase = phase.strip().lower()
    payload = anchor_payload(
        phase=normalized_phase,
        summary=summary.strip(),
        tasks=_serialize_subtasks(runtime.plan.subtasks),
        completion_pct=completion_pct,
    )
    _append_session_event("phase.anchor", payload)
    _update_session_phase(normalized_phase)


def _tool_response(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
    return {"content": [{"type": "text", "text": text}]}


def _pane_id_for_task(task_name: str) -> int | None:
    for st in _runtime().plan.subtasks:
        if st.name == task_name:
            return st.pane_id
    return None


ROLE_TEMPLATES: dict[str, str] = {
    "reviewer": (
        "## Role: Reviewer\n\n"
        "You are a code reviewer. Your analysis should cover:\n"
        "- Code density: information per line of code. Flag low-density padding.\n"
        "- Naming precision: do names reveal intent without reading the body?\n"
        "- Composition patterns: classes that should be pipelines, inheritance → composition\n"
        "- Specific merge suggestions: 'functions X, Y → one pipeline'\n"
        "- DRY violations: duplicated logic that should be composed\n"
        "{violations_block}\n"
        "Rate 1-10: density, DRY, naming, composition.\n"
        "Output structured critique with severity (critical/major/minor). "
        "Do NOT commit — only report findings."
    ),
    "challenger": (
        "## Role: Challenger\n\n"
        "You are a technical challenger. Propose a RADICALLY SIMPLER alternative:\n"
        "- Write pseudocode of a version that's 50% less code (MANDATORY)\n"
        "- Question every class — propose the function-based alternative\n"
        "- Question every abstraction used <2 places — inline it\n"
        "- Identify the simplest design: fewest files, fewest functions\n"
        "- Propose specific merges: 'X, Y, Z → one pipeline'\n"
        "Do NOT modify code — provide analysis with counter-design pseudocode."
    ),
    "debugger": (
        "## Role: Debugger\n\n"
        "You are a debugger. Diagnose the root cause of failures, trace execution paths, "
        "and propose targeted fixes. You may commit fixes if instructed to do so."
    ),
}


def _build_role_context(role: str) -> str:
    """Return role-specific instructions to inject into an agent prompt.

    Returns empty string for the default 'writer' role.
    """
    return ROLE_TEMPLATES.get(role, "")


def _build_employee_context(employee_name: str | None) -> str:
    """Load employee profile and build prompt context block. Empty string if not found."""
    if not employee_name:
        return ""
    from openmax.employees import build_employee_context, get_employee

    emp = get_employee(employee_name)
    if emp is None:
        return ""
    return build_employee_context(emp)


def _build_blackboard_block(cwd: str) -> str:
    content = read_shared_context(cwd)
    if not content:
        return ""
    return (
        "\n\n## Shared Blackboard\n\n"
        "Read before making architectural decisions:\n\n" + content[:3000]
    )


_POLL_INITIAL = 0.15
_POLL_BACKOFF = 1.5
_POLL_MAX = 1.0


async def _wait_for_pane_ready(
    pane_mgr: PaneManager,
    pane_id: int,
    ready_patterns: list[str],
    timeout: float = 30.0,
    poll_interval: float = _POLL_INITIAL,
) -> bool:
    """Poll pane output until a ready pattern appears or timeout.

    Uses two strategies:
    1. Pattern match — look for known ready strings in pane output.
    2. Output stability — if the pane has substantial output (≥3 lines)
       and output hasn't changed for 2 consecutive checks, treat as ready.
       This handles CLI version changes where patterns shift.

    Polling uses adaptive backoff: starts fast (0.15s), slows on stable
    output, resets when output changes.
    """
    if not ready_patterns:
        return False
    deadline = time.monotonic() + timeout
    prev_text = ""
    stable_count = 0
    interval = poll_interval
    while time.monotonic() < deadline:
        try:
            text = pane_mgr.get_text(pane_id)
        except Exception:
            text = ""
        if any(pat in text for pat in ready_patterns):
            return True
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        if len(lines) >= 3 and text == prev_text:
            stable_count += 1
            if stable_count >= 2:
                return True
            interval = min(interval * _POLL_BACKOFF, _POLL_MAX)
        else:
            stable_count = 0
            interval = poll_interval  # reset on new output
        prev_text = text
        await anyio.sleep(interval)
    return False


def _extract_smart_output(text: str, tail_lines: int = 100) -> str:
    """Return tail of output, with error lines from earlier surfaced at top."""
    lines = text.splitlines()
    tail = lines[-tail_lines:]
    error_kw = ["Error", "error", "Traceback", "FAILED", "fatal", "exception", "❌"]
    error_context = [
        f"[ERROR] {line.strip()}"
        for line in lines[:-tail_lines]
        if any(k in line for k in error_kw)
    ][-20:]
    if error_context:
        return "\n".join(error_context) + "\n---\n" + "\n".join(tail)
    return "\n".join(tail)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07")
_PROGRESS_RE = re.compile(r"[\u2500-\u257F\u2580-\u259F\u2588\u2591-\u2593━─╸╺]+")
_SPINNER_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷◐◓◑◒]")


def strip_terminal_noise(text: str) -> str:
    """Remove ANSI escapes, progress bars, and spinner chars for clean hashing."""
    text = _ANSI_RE.sub("", text)
    text = _PROGRESS_RE.sub("", text)
    text = _SPINNER_RE.sub("", text)
    return text


def _compress_context(context: str, budget: int) -> str:
    """Compress context text to fit within an approximate token budget.

    Uses len(text)//4 as a rough token estimate. If over budget, keeps the
    first paragraph and as many subsequent bullet/numbered-list lines as fit.
    """
    approx_tokens = len(context) // 4
    if approx_tokens <= budget:
        return context

    char_budget = budget * 4
    lines = context.split("\n")

    kept: list[str] = []
    i = 0
    while i < len(lines):
        kept.append(lines[i])
        if lines[i].strip() == "" and i > 0:
            i += 1
            break
        i += 1

    for line in lines[i:]:
        stripped = line.lstrip()
        is_key_line = stripped.startswith(("-", "*", "#")) or (
            len(stripped) > 1 and stripped[0].isdigit() and stripped[1] in ".)"
        )
        if not is_key_line:
            continue
        candidate = "\n".join(kept + [line])
        if len(candidate) <= char_budget:
            kept.append(line)
        else:
            break

    result = "\n".join(kept)
    if len(result) > char_budget:
        result = result[:char_budget].rsplit("\n", 1)[0]
    return result


def _build_subagent_context(
    *,
    branch_name: str | None,
    agent_cwd: str | None = None,
) -> str:
    """Build a structured context block for sub-agent prompts.

    Returns empty string when there is nothing to inject.
    """
    sections: list[str] = []

    if agent_cwd:
        sections.append(
            f"Working directory: {agent_cwd}\n"
            f"You are already in the correct directory. Do NOT run `cd`."
        )

    if branch_name:
        sections.append(
            f"Branch: {branch_name} (isolated worktree — commit here, do not switch branches)"
        )

    if not sections:
        return ""

    header = "## Context (auto-injected by openMax — use only if relevant)"
    return "\n\n" + header + "\n\n" + "\n\n".join(sections)


def _open_new_agent_window(
    runtime: LeadAgentRuntime,
    command: list[str] | str,
    purpose: str,
    agent_type: str,
    title: str | None,
    cwd: str,
    env: dict[str, str] | None,
) -> SimpleNamespace:
    """Create a new window when the current one is full, and update agent_window_id."""
    console.print("  [yellow]![/yellow]  Terminal full — opening new window for overflow panes")
    pane = runtime.pane_mgr.create_window(
        command=command,
        purpose=purpose,
        agent_type=agent_type,
        title=title,
        cwd=cwd,
        env=env,
    )
    runtime.agent_window_id = pane.window_id
    return pane


def _launch_pane(
    runtime: LeadAgentRuntime,
    command: list[str] | str,
    purpose: str,
    agent_type: str = "tool",
    title: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stream_json: bool = False,
) -> SimpleNamespace:
    effective_cwd = cwd or runtime.cwd
    stream_kw: dict[str, bool] = {"stream_json": True} if stream_json else {}
    if runtime.agent_window_id is None:
        pane = runtime.pane_mgr.create_window(
            command=command,
            purpose=purpose,
            agent_type=agent_type,
            title=title,
            cwd=effective_cwd,
            env=env,
            **stream_kw,
        )
        runtime.agent_window_id = pane.window_id
        return pane
    try:
        return runtime.pane_mgr.add_pane(
            window_id=runtime.agent_window_id,
            command=command,
            purpose=purpose,
            agent_type=agent_type,
            title=title,
            cwd=effective_cwd,
            env=env,
            **stream_kw,
        )
    except PaneBackendError as e:
        if "No space" not in str(e):
            raise
        return _open_new_agent_window(
            runtime, command, purpose, agent_type, title, effective_cwd, env
        )


def _safe_launch_pane(
    runtime: LeadAgentRuntime,
    *,
    command: list[str] | str,
    purpose: str,
    agent_type: str,
    title: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stream_json: bool = False,
) -> tuple[SimpleNamespace | None, str | None]:
    """Launch a pane with error handling. Returns (pane, error_message)."""
    try:
        pane = _launch_pane(
            runtime,
            command=command,
            purpose=purpose,
            agent_type=agent_type,
            title=title,
            cwd=cwd,
            env=env,
            stream_json=stream_json,
        )
        return pane, None
    except PaneBackendError as e:
        return None, f"Pane backend error: {e}"
    except RuntimeError as e:
        return None, f"Pane launch failed: {e}"
    except OSError as e:
        return None, f"OS error during pane launch: {e}"


def _try_reuse_done_pane(
    runtime: LeadAgentRuntime, agent_type: str, task_name: str
) -> SimpleNamespace | None:
    """Find a done pane with the same agent type to reuse.

    Only reuses a pane if no other running/pending task is already occupying it.
    This prevents multiple parallel tasks from being funneled into the same pane.
    """
    busy_panes: set[int] = set()
    for st in runtime.plan.subtasks:
        if st.status in (TaskStatus.RUNNING, TaskStatus.PENDING) and st.pane_id is not None:
            busy_panes.add(st.pane_id)

    alive = runtime.pane_mgr.alive_pane_ids()
    for st in runtime.plan.subtasks:
        if (
            st.agent_type == agent_type
            and st.status == TaskStatus.DONE
            and st.pane_id is not None
            and st.pane_id not in busy_panes
            and st.pane_id in alive
        ):
            console.print(f"  [dim]{P}  reusing pane {st.pane_id} for {task_name}[/dim]")
            return SimpleNamespace(
                pane_id=st.pane_id,
                window_id=runtime.agent_window_id,
            )
    return None


async def _wait_and_send_prompt(
    runtime: LeadAgentRuntime,
    pane: SimpleNamespace,
    cmd_spec: Any,
    agent_type: str,
) -> bool:
    """Wait for CLI ready then send the initial prompt. Returns True if ready detected."""
    trust_patterns = getattr(cmd_spec, "trust_patterns", None) or []
    if trust_patterns:
        await _auto_accept_trust(runtime.pane_mgr, pane.pane_id, trust_patterns)

    if not cmd_spec.initial_input:
        return True

    ready = True
    if cmd_spec.ready_patterns:
        ready = await _wait_for_pane_ready(
            runtime.pane_mgr,
            pane.pane_id,
            cmd_spec.ready_patterns,
            timeout=max(cmd_spec.ready_delay_seconds * 4, 30.0),
        )
        if not ready:
            console.print(
                f"  [yellow]![/yellow]Pane {pane.pane_id} ({agent_type}) "
                "did not show ready signal within timeout — sending prompt anyway"
            )
    else:
        await anyio.sleep(cmd_spec.ready_delay_seconds)
    runtime.pane_mgr.send_text(pane.pane_id, cmd_spec.initial_input)
    return ready


async def _auto_accept_trust(
    pane_mgr: PaneManager,
    pane_id: int,
    trust_patterns: list[str],
    max_polls: int = 3,
) -> None:
    """Poll for a trust/confirmation dialog and press Enter to accept.

    Short timeout (~0.6s) since worktrees get pre-trusted settings.
    """
    for i in range(max_polls):
        await anyio.sleep(0.15 if i < 2 else 0.3)
        try:
            text = pane_mgr.get_text(pane_id)
        except Exception:
            continue
        if any(pat in text for pat in trust_patterns):
            pane_mgr.send_text(pane_id, "", submit=True)
            console.print(f"  [dim]{P}  auto-accepted trust dialog on pane {pane_id}[/dim]")
            return


def _read_subtask_report(task_name: str) -> str | None:
    """Try to read a subtask's completion report from known locations."""
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.name == task_name and st.branch_name:
            wt = str(Path(runtime.cwd) / ".openmax-worktrees" / st.branch_name.replace("/", "_"))
            report = read_report(wt, task_name)
            if report:
                return report
    return read_report(runtime.cwd, task_name)


def _persist_report_to_main(runtime: LeadAgentRuntime, task_name: str, text: str) -> None:
    """Copy report to main cwd so it survives worktree cleanup."""
    if read_report(runtime.cwd, task_name) is None:
        rp = report_path(runtime.cwd, task_name)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(text, encoding="utf-8")


def _save_pane_log(runtime: LeadAgentRuntime, st: SubTask) -> Path | None:
    """Save full pane output to .openmax/logs/{task_name}.log (up to 2000 lines)."""
    try:
        text = runtime.pane_mgr.get_text(st.pane_id)
    except Exception:
        return None
    if not text or len(text.strip()) < 20:
        return None
    lines = text.splitlines()
    log_dir = Path(runtime.cwd) / ".openmax" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{st.name}.log"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def _synthesize_report_from_pane(runtime: LeadAgentRuntime, st: SubTask) -> str | None:
    """Fallback: synthesize a minimal report from the saved pane log."""
    log_path = Path(runtime.cwd) / ".openmax" / "logs" / f"{st.name}.log"
    if not log_path.exists():
        return None
    content = log_path.read_text(encoding="utf-8")
    tail = _extract_smart_output(content, tail_lines=50)
    log_rel = log_path.relative_to(runtime.cwd)
    return (
        f"## Status\ndone (auto-synthesized)\n\n"
        f"## Full Log\n`{log_rel}`\n\n"
        f"## Output (last lines)\n```\n{tail}\n```"
    )


def _read_subtask_report_for_pane(pane_id: int) -> str | None:
    runtime = _runtime()
    for st in runtime.plan.subtasks:
        if st.pane_id == pane_id:
            return _read_subtask_report(st.name)
    return None


def _resolve_session_id() -> str | None:
    try:
        rt = _runtime()
        if rt.session_meta:
            return rt.session_meta.session_id
    except RuntimeError:
        pass
    return None


def _build_identity_block(task_name: str, session_id: str | None) -> str:
    """Build identity + communication block — placed at the top of agent prompts."""
    sid = session_id or "unknown"
    block = f"\n\n## openMax Task\n\nTask: {task_name} | Session: {sid}"
    if session_id:
        block += (
            f'\n\nUse MCP tools from `openmax` server with session_id="{sid}":\n'
            f'- report_progress(task="{task_name}", pct=<0-100>, msg="...")\n'
            f'- report_done(task="{task_name}", summary="...", '
            f"input_tokens=N, output_tokens=N, cost_usd=X.XX)\n"
            f"  Include your token usage stats if available."
        )
    return block


def _file_protocol_section(rep_file: Path, cwd: str) -> str:
    """Build the file protocol instructions to append to agent prompts."""
    report_rel = rep_file.relative_to(cwd)
    return (
        f"\n\nWrite completion report to `{report_rel}` (Status, Summary, Changes, Test Results)."
    )
