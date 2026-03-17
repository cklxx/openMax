from __future__ import annotations

import sys
import time

import anyio
from click.testing import CliRunner

from openmax import cli
from openmax.adapters.subprocess_adapter import SubprocessAdapter
from openmax.agent_registry import AgentDefinition, AgentRegistry
from openmax.lead_agent import PlanResult
from openmax.lead_agent import tools as lead_agent_tools
from openmax.lead_agent.runtime import (
    LeadAgentRuntime,
    bind_lead_agent_runtime,
    reset_lead_agent_runtime,
)
from openmax.memory import MemoryStore
from openmax.pane_manager import PaneManager
from openmax.session_runtime import SessionStore


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def test_ci_smoke_exercises_headless_noninteractive_dispatch_and_session_cli(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("OPENMAX_PANE_BACKEND", "headless")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    pane_mgr = PaneManager()
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    session_meta = session_store.create_session(
        "ci-smoke-session",
        "Run CI smoke",
        str(workspace),
    )
    runtime = LeadAgentRuntime(
        cwd=str(workspace),
        plan=PlanResult(goal="Run CI smoke"),
        pane_mgr=pane_mgr,
        session_store=session_store,
        session_meta=session_meta,
        memory_store=MemoryStore(base_dir=tmp_path / "memory"),
        agent_registry=AgentRegistry(
            [
                AgentDefinition(
                    name="ci-smoke",
                    adapter=SubprocessAdapter(
                        name="ci-smoke",
                        command_template=[
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import sys; "
                                "print('CI_SMOKE_START', flush=True); "
                                "print(sys.argv[1], flush=True)"
                            ),
                            "{prompt}",
                        ],
                        is_interactive=False,
                    ),
                    source="test",
                    built_in=False,
                )
            ]
        ),
    )
    token = bind_lead_agent_runtime(runtime)
    runner = CliRunner()

    def fail_send_text(*_args, **_kwargs):
        raise AssertionError("non-interactive smoke agent should not receive send_text")

    pane_mgr.send_text = fail_send_text  # type: ignore[method-assign]

    try:
        result = anyio.run(
            lead_agent_tools.dispatch_agent.handler,
            {
                "task_name": "Smoke task",
                "agent_type": "ci-smoke",
                "prompt": "print session smoke output",
            },
        )

        assert '"status": "dispatched"' in result["content"][0]["text"]
        assert runtime.agent_window_id is not None

        pane_id = runtime.plan.subtasks[0].pane_id
        assert pane_id is not None
        _wait_until(
            lambda: "print session smoke output" in pane_mgr.get_text(pane_id),
        )

        anyio.run(
            lead_agent_tools.record_phase_anchor.handler,
            {
                "phase": "dispatch",
                "summary": "Headless smoke agent completed",
                "completion_pct": 50,
            },
        )
        anyio.run(
            lead_agent_tools.mark_task_done.handler,
            {
                "task_name": "Smoke task",
            },
        )
        anyio.run(
            lead_agent_tools.report_completion.handler,
            {
                "completion_pct": 100,
                "notes": "CI smoke completed",
            },
        )

        monkeypatch.setattr(cli, "SessionStore", lambda: session_store)

        runs_result = runner.invoke(cli.main, ["runs"])
        inspect_result = runner.invoke(cli.main, ["inspect", session_meta.session_id])

        assert runs_result.exit_code == 0
        assert "ci-smoke-ses" in runs_result.output  # truncated to 12 chars in table
        assert "100%" in runs_result.output

        assert inspect_result.exit_code == 0
        assert "ci-smoke-session" in inspect_result.output
        assert "Headless smoke agent completed" in inspect_result.output
        assert "Smoke task" in inspect_result.output
        assert "ci-smoke" in inspect_result.output
        assert "active" in inspect_result.output
    finally:
        pane_mgr.cleanup_all()
        reset_lead_agent_runtime(token)
