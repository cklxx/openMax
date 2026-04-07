"""Integration tests hitting real APIs and real backends.

Run with: OPENMAX_E2E=1 pytest tests/test_integration.py -v -s --timeout=600

Covers:
- File-based task prompt (@file) → real lead agent
- LLM-based multi-task decomposition (real Claude call)
- Multi-agent dispatch via headless backend
- Tmux backend spawn/read/session recovery
- Error propagation end-to-end
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from openmax.adapters import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, AgentRegistry
from openmax.lead_agent.core import run_lead_agent
from openmax.pane_backend import HeadlessPaneBackend
from openmax.pane_manager import PaneManager
from openmax.task_runner import split_multi_tasks

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENMAX_E2E"),
    reason="Set OPENMAX_E2E=1 to run real E2E integration tests",
)

# ── Sub-agent script ─────────────────────────────────────────────────────────

_TASK_AGENT_SCRIPT = """\
import os, pathlib, sys
cwd = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
marker = os.environ.get("OPENMAX_MARKER", "")
if marker:
    pathlib.Path(marker).write_text("done")
reports = cwd / ".openmax" / "reports"
reports.mkdir(parents=True, exist_ok=True)
briefs = cwd / ".openmax" / "briefs"
if briefs.exists():
    for brief in briefs.glob("*.md"):
        (reports / brief.name).write_text(
            "## Status\\ndone\\n\\n## Summary\\nAll checks passed.\\n"
            "\\n## Changes\\n- None\\n\\n## Test Results\\nPASS\\n"
        )
print("Task completed successfully. All work is done.")
"""


def _make_registry(marker_path: str) -> AgentRegistry:
    adapter = SubprocessAdapter(
        name="task-agent",
        command_template=["python3", "-c", _TASK_AGENT_SCRIPT, "{cwd}"],
        is_interactive=False,
        env={"OPENMAX_MARKER": marker_path},
    )
    defn = AgentDefinition(name="task-agent", adapter=adapter, source="integration-test")
    return AgentRegistry([defn])


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _quick_cmd(text: str) -> list[str]:
    return [sys.executable, "-u", "-c", f"import time; print({text!r}, flush=True); time.sleep(60)"]


# ── File input → real lead agent ─────────────────────────────────────────────


def test_file_prompt_dispatches_agent(tmp_path):
    """Write a task prompt to a file, pass it via @file, and verify agent runs."""
    task_file = tmp_path / "task.md"
    task_file.write_text(
        "Run a quick health check on this repository using task-agent. "
        "Dispatch exactly one sub-agent and report the results."
    )
    prompt = task_file.read_text().strip()

    marker = str(tmp_path / "done.marker")
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())

    result = run_lead_agent(
        task=prompt,
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["task-agent"],
        agent_registry=_make_registry(marker),
        max_turns=15,
        plan_confirm=False,
    )

    done = sum(1 for t in result.subtasks if t.status.value == "done")
    assert done >= 1, f"Expected ≥1 done subtask; got: {result.subtasks}"
    assert os.path.exists(marker), "task-agent never ran"


# ── Real LLM task decomposition ──────────────────────────────────────────────


def test_llm_splits_multiple_independent_tasks():
    """Real Claude call decomposes prose into multiple independent tasks."""
    text = (
        "Fix the authentication bug where users get logged out after 5 minutes, "
        "add pagination support to the /api/users endpoint, "
        "and write comprehensive unit tests for the payment processing module."
    )
    tasks = split_multi_tasks(text)
    assert len(tasks) >= 2, f"Expected ≥2 tasks, got {len(tasks)}: {tasks}"


def test_llm_does_not_return_empty():
    """Real Claude call never returns an empty list for a substantial prompt."""
    text = (
        "Refactor the authentication module to replace session-based auth with JWT tokens, "
        "updating all middleware, tests, and documentation to match the new approach."
    )
    tasks = split_multi_tasks(text)
    assert len(tasks) >= 1, f"Expected ≥1 tasks, got {len(tasks)}: {tasks}"
    assert all(t.strip() for t in tasks), f"Empty task string in result: {tasks}"


def test_llm_decomposition_with_chinese_input():
    """Real Claude call handles Chinese input without error."""
    text = (
        "修复登录页面的认证 bug，给 /api/users 接口加上分页功能，"
        "然后给支付模块写集成测试。这三个任务互相独立。"
    )
    tasks = split_multi_tasks(text)
    # LLM may or may not split Chinese — just verify no crash and non-empty result
    assert len(tasks) >= 1, f"Expected ≥1 tasks, got {len(tasks)}: {tasks}"
    assert all(t.strip() for t in tasks), f"Empty task string in result: {tasks}"


# ── Multi-agent dispatch (headless) ──────────────────────────────────────────


def test_multi_agent_dispatch_headless(tmp_path):
    """Lead agent dispatches multiple sub-agents to separate headless panes."""
    marker = str(tmp_path / "done.marker")
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())

    task = (
        "Execute the following 2 INDEPENDENT tasks in parallel.\n"
        "Each task should be dispatched as a separate sub-agent.\n"
        "All tasks are independent — no dependencies between them.\n\n"
        "1. Run a health check on the repository using task-agent\n"
        "2. Run a quick verification on the repository using task-agent"
    )

    result = run_lead_agent(
        task=task,
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["task-agent"],
        agent_registry=_make_registry(marker),
        max_turns=20,
        plan_confirm=False,
    )

    done = sum(1 for t in result.subtasks if t.status.value == "done")
    assert done >= 1, f"Expected ≥1 done subtask; got: {result.subtasks}"
    assert len(result.subtasks) >= 2, f"Expected ≥2 subtasks dispatched; got {len(result.subtasks)}"


# ── Tmux backend ─────────────────────────────────────────────────────────────


_TMUX_SKIP = pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not installed")


@_TMUX_SKIP
class TestTmuxIntegration:
    """Real tmux backend operations on an isolated server."""

    @pytest.fixture()
    def tmux(self):
        from openmax.pane_backend import TmuxPaneBackend

        sock = f"openmax_integ_{uuid.uuid4().hex[:8]}"
        session = "integ_test"
        subprocess.run(
            ["tmux", "-L", sock, "new-session", "-d", "-s", session, "-x", "120", "-y", "40"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        backend = TmuxPaneBackend(socket_name=sock, target_session=session)
        yield backend
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=5)

    def test_spawn_read_kill(self, tmux):
        """Spawn a pane, read output, kill it."""
        pane_id = tmux.spawn_window(_quick_cmd("tmux_integ"))
        _wait_until(lambda: "tmux_integ" in tmux.get_text(pane_id))
        tmux.kill_pane(pane_id)

    def test_multi_pane_parallel(self, tmux):
        """Spawn multiple panes and verify all produce output."""
        pids = []
        for i in range(3):
            pid = tmux.spawn_window(_quick_cmd(f"agent_{i}"))
            pids.append(pid)

        for i, pid in enumerate(pids):
            _wait_until(lambda pid=pid, i=i: f"agent_{i}" in tmux.get_text(pid))

        panes = tmux.list_panes()
        active_ids = {p.pane_id for p in panes}
        for pid in pids:
            assert pid in active_ids

    def test_send_text_to_interactive_pane(self, tmux):
        """Send text + enter to an interactive process and read echoed output."""
        cmd = [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('ready', flush=True); "
            "line = sys.stdin.readline().strip(); "
            "print(f'ECHO:{line}', flush=True); "
            "import time; time.sleep(60)",
        ]
        pane_id = tmux.spawn_window(cmd)
        _wait_until(lambda: "ready" in tmux.get_text(pane_id))

        tmux.send_text(pane_id, "hello_from_test")
        tmux.send_enter(pane_id)
        _wait_until(lambda: "ECHO:hello_from_test" in tmux.get_text(pane_id))

    def test_session_recovery(self, tmux):
        """After session is externally killed, spawn_window recreates it."""
        if not tmux._target_session:
            pytest.skip("needs target session")

        tmux._run_tmux(["kill-session", "-t", tmux._target_session], check=False)
        time.sleep(0.2)

        pane_id = tmux.spawn_window(_quick_cmd("recovered"))
        _wait_until(lambda: "recovered" in tmux.get_text(pane_id))


# ── Headless backend concurrent ──────────────────────────────────────────────


def test_headless_concurrent_panes():
    """Multiple panes run concurrently on the headless backend."""
    backend = HeadlessPaneBackend()
    pids = [backend.spawn_window(_quick_cmd(f"h_{i}"), cwd="/tmp") for i in range(5)]

    for i, pid in enumerate(pids):
        _wait_until(lambda pid=pid, i=i: f"h_{i}" in backend.get_text(pid))

    assert len(backend.list_panes()) == 5

    for pid in pids:
        backend.kill_pane(pid)

    _wait_until(lambda: backend.list_panes() == [])


# ── Parallel cases with sequential internal workflow ─────────────────────────


def test_parallel_cases_with_sequential_steps(tmp_path):
    """Verify openMax decomposes 'N parallel cases, each with sequential steps' correctly.

    This models a render pipeline:
      Case 1: check → parse → render  (sequential)
      Case 2: check → parse → render  (sequential)
      Case 3: check → parse → render  (sequential)
    All three cases run in parallel; steps within each case are sequential.

    The lead agent should produce a plan with dependencies like:
      case1-check (no deps)  →  case1-parse (dep: case1-check)  →  case1-render (dep: case1-parse)
      case2-check (no deps)  →  case2-parse (dep: case2-check)  →  ...
      case3-check (no deps)  →  case3-parse (dep: case3-check)  →  ...
    With parallel_groups containing all check tasks.
    """
    marker = str(tmp_path / "done.marker")
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())

    task = (
        "Process 3 independent code samples in parallel. "
        "For EACH sample, execute these steps IN ORDER (sequential within each case):\n"
        "  Step 1: Renderability check — verify the code is runnable\n"
        "  Step 2: Parse the code into a structured format\n"
        "  Step 3: Render the final output\n\n"
        "The 3 samples are completely independent — run all 3 cases in parallel, "
        "but within each case, step 1 must finish before step 2, "
        "and step 2 must finish before step 3.\n\n"
        "Use task-agent for all steps. Report results when all 3 cases complete."
    )

    result = run_lead_agent(
        task=task,
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["task-agent"],
        agent_registry=_make_registry(marker),
        max_turns=40,
        plan_confirm=False,
    )

    # Verify: at least 3 subtasks were created (one per case minimum)
    assert len(result.subtasks) >= 3, (
        f"Expected ≥3 subtasks for 3 cases; got {len(result.subtasks)}: "
        f"{[st.name for st in result.subtasks]}"
    )

    # Verify: at least some subtasks have dependencies (sequential chaining)
    deps_present = any(st.dependencies for st in result.subtasks)
    assert deps_present, (
        "Expected sequential dependencies within cases, but no subtask has dependencies: "
        f"{[(st.name, st.dependencies) for st in result.subtasks]}"
    )

    # Verify: at least 1 subtask completed
    done = sum(1 for t in result.subtasks if t.status.value == "done")
    statuses = [f"{st.name}:{st.status.value}" for st in result.subtasks]
    assert done >= 1, f"Expected ≥1 done subtask; got: {statuses}"


# ── Execute don't just build ──────────────────────────────────────────────────


def test_execute_on_data_not_just_build_script(tmp_path):
    """Lead agent must execute pipeline on each data item, not just write a script.

    We create 3 data files and ask the agent to "process each one".
    The agent should dispatch sub-agents that actually touch each file,
    not just write a generic pipeline script and stop.
    """
    # Create data files for the agent to process
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for i in range(3):
        (data_dir / f"sample_{i}.txt").write_text(f"raw data for sample {i}")

    marker = str(tmp_path / "done.marker")
    pane_mgr = PaneManager(backend=HeadlessPaneBackend())

    task = (
        f"Process each data file in {data_dir}/ individually using task-agent.\n"
        f"There are 3 files: sample_0.txt, sample_1.txt, sample_2.txt.\n"
        "For each file: read it, validate it, and write a result.\n\n"
        "IMPORTANT: Dispatch a separate sub-agent for each file. "
        "Do NOT just write a script — actually process each file via sub-agents."
    )

    result = run_lead_agent(
        task=task,
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["task-agent"],
        agent_registry=_make_registry(marker),
        max_turns=25,
        plan_confirm=False,
    )

    # Must have dispatched at least 3 subtasks (one per data file)
    assert len(result.subtasks) >= 3, (
        f"Expected ≥3 subtasks (one per data file); got {len(result.subtasks)}: "
        f"{[st.name for st in result.subtasks]}"
    )

    # At least 1 must have completed
    done = sum(1 for t in result.subtasks if t.status.value == "done")
    assert done >= 1, (
        f"Expected ≥1 done subtask; got: "
        f"{[f'{st.name}:{st.status.value}' for st in result.subtasks]}"
    )


# ── Error propagation ────────────────────────────────────────────────────────


def test_error_in_agent_visible_to_lead_agent(tmp_path):
    """When a sub-agent fails, the lead agent sees the error and reports it."""
    failing_script = """\
import sys
print("Starting task...", flush=True)
raise RuntimeError("Intentional failure for testing")
"""
    adapter = SubprocessAdapter(
        name="failing-agent",
        command_template=["python3", "-c", failing_script],
        is_interactive=False,
    )
    registry = AgentRegistry(
        [AgentDefinition(name="failing-agent", adapter=adapter, source="test")]
    )

    pane_mgr = PaneManager(backend=HeadlessPaneBackend())
    result = run_lead_agent(
        task="Run a quick check using failing-agent and report the results.",
        pane_mgr=pane_mgr,
        cwd=str(tmp_path),
        allowed_agents=["failing-agent"],
        agent_registry=registry,
        max_turns=15,
        plan_confirm=False,
    )

    # The lead agent should have attempted at least one subtask
    assert len(result.subtasks) >= 1
