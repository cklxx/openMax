from __future__ import annotations

from dataclasses import asdict
import json

from openmax.memory_system import MemoryEntry, MemoryStore, infer_code_scope


def test_memory_store_builds_relevant_context(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    store.record_lesson(
        cwd=cwd,
        task="Build API routes",
        lesson="Prefer codex for API surface changes.",
        rationale="It produced cleaner route scaffolding in the last run.",
        confidence=8,
    )
    store.record_run_summary(
        cwd=cwd,
        task="Build API routes",
        notes="API delivery succeeded after one focused dispatch.",
        completion_pct=100,
        subtasks=[
            {"name": "API routes", "agent_type": "codex", "status": "done"},
        ],
        anchors=[{"summary": "Dispatch API work to codex first."}],
    )

    context = store.build_context(cwd=cwd, task="Refactor API routes")

    assert context is not None
    assert context.matched_entries >= 1
    assert "Prefer codex for API surface changes." in context.text
    assert "Learned memory for this workspace:" in context.text
    assert "Workspace facts:" in context.text
    assert "Dispatch API work to codex first." in context.text


def test_render_workspace_memories_lists_recent_entries(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    store.record_lesson(
        cwd=cwd,
        task="Build docs",
        lesson="Keep docs changes isolated from runtime changes.",
        confidence=7,
    )

    lines = store.render_workspace_memories(cwd, limit=5)

    assert lines
    assert "Strategy:" in lines[0]
    assert any("Keep docs changes isolated" in line for line in lines)


def test_memory_store_derives_agent_and_risk_guidance(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    store.record_lesson(
        cwd=cwd,
        task="Refactor API routes",
        lesson="Avoid generic for API refactors when tests are required.",
        rationale="It drifted and needed retries.",
        confidence=8,
    )
    store.record_run_summary(
        cwd=cwd,
        task="Build API routes",
        notes="Codex handled the API route scaffold cleanly.",
        completion_pct=100,
        subtasks=[
            {"name": "API routes", "agent_type": "codex", "status": "done"},
        ],
        anchors=[{"summary": "codex was the fastest route authoring agent."}],
    )

    context = store.build_context(cwd=cwd, task="Implement new API route tests")

    assert context is not None
    assert "Recommended agent choices:" in context.text
    assert "Prefer codex" in context.text
    assert "Known risks:" in context.text
    assert "Avoid generic for API refactors" in context.text


def test_infer_code_scope_and_rankings_favor_same_code_work(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    store.record_run_summary(
        cwd=cwd,
        task="Implement src/api/routes.py tests",
        notes="Codex handled the API route scaffold cleanly.",
        completion_pct=100,
        subtasks=[
            {
                "name": "API routes",
                "agent_type": "codex",
                "status": "done",
                "prompt": "Update src/api/routes.py and tests/test_routes.py",
            },
        ],
        anchors=[{"summary": "API routes work stayed in src/api/routes.py"}],
    )
    store.record_run_summary(
        cwd=cwd,
        task="Refresh docs landing page",
        notes="Claude-code was strong for docs/index.html polishing.",
        completion_pct=100,
        subtasks=[
            {
                "name": "Docs page",
                "agent_type": "claude-code",
                "status": "done",
                "prompt": "Update docs/index.html hero section",
            },
        ],
        anchors=[{"summary": "Docs work stayed in docs/index.html"}],
    )

    scope = infer_code_scope("Refactor src/api/routes.py tests")
    rankings = store.derive_agent_rankings(cwd=cwd, task="Refactor src/api/routes.py tests")

    assert "routes.py" in scope or "api" in scope
    assert rankings
    assert rankings[0].agent_type == "codex"
    assert rankings[0].reasons


def test_memory_store_persists_structured_fields(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    lesson = store.record_lesson(
        cwd=cwd,
        task="Build API routes",
        lesson="Prefer codex for API surface changes.",
        rationale="It produced cleaner route scaffolding in the last run.",
        confidence=8,
    )
    run = store.record_run_summary(
        cwd=cwd,
        task="Build API routes",
        notes="API delivery succeeded after one focused dispatch.",
        completion_pct=100,
        subtasks=[
            {
                "name": "API routes",
                "agent_type": "codex",
                "status": "done",
                "prompt": "Update src/api/routes.py",
            },
        ],
        anchors=[{"summary": "API routes work stayed in src/api/routes.py"}],
    )

    entries = store.load_entries(cwd)

    assert lesson.lessons == ["Prefer codex for API surface changes."]
    assert run.workspace_facts
    assert "API routes work stayed in src/api/routes.py" in run.workspace_facts
    assert run.performance_signals
    assert run.performance_signals[0]["agent_type"] == "codex"
    assert entries[-1].workspace_facts == run.workspace_facts
    assert entries[-1].performance_signals == run.performance_signals


def test_memory_entry_round_trips_agent_stats_from_payload():
    payload = {
        "memory_id": "run-123",
        "created_at": "2026-03-10T00:00:00+00:00",
        "kind": "run_summary",
        "task": "Refactor API routes",
        "summary": "Captured structured memory for API route work.",
        "workspace_facts": ["Relevant scope: api, routes.py"],
        "lessons": ["Keep route updates isolated from docs changes."],
        "performance_signals": [
            {
                "agent_type": "codex",
                "status": "done",
                "task_name": "API routes",
                "prompt": "Update src/api/routes.py",
                "outcome": "positive",
                "detail": "codex completed 'API routes'",
            }
        ],
        "agent_stats": [
            {
                "agent_type": "codex",
                "success_count": 2,
                "failure_count": 0,
                "incomplete_count": 0,
                "total_count": 2,
                "success_rate": 1.0,
                "detail": "codex succeeded on 2 of 2 similar subtasks",
            }
        ],
        "metadata": {"code_scope": ["api", "routes.py"]},
    }

    entry = MemoryEntry.from_payload(payload)

    assert entry.workspace_facts == payload["workspace_facts"]
    assert entry.lessons == payload["lessons"]
    assert entry.agent_stats == payload["agent_stats"]
    assert asdict(entry)["agent_stats"] == payload["agent_stats"]


def test_agent_rankings_use_explicit_agent_stats_from_structured_memory(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")
    workspace_path = store._workspace_path(cwd)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text(
        json.dumps(
            {
                "cwd": cwd,
                "entries": [
                    {
                        "memory_id": "stats-codex",
                        "created_at": "2026-03-10T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py tests",
                        "summary": "Structured feedback captured for API route work.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
                        "agent_stats": [
                            {
                                "agent_type": "codex",
                                "success_count": 3,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 3,
                                "success_rate": 1.0,
                                "detail": "codex succeeded on 3 of 3 similar subtasks",
                            }
                        ],
                        "metadata": {"code_scope": ["api", "routes.py", "tests"]},
                    },
                    {
                        "memory_id": "stats-generic",
                        "created_at": "2026-03-11T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py tests",
                        "summary": "Structured feedback captured for API route work.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
                        "agent_stats": [
                            {
                                "agent_type": "generic",
                                "success_count": 0,
                                "failure_count": 2,
                                "incomplete_count": 1,
                                "total_count": 3,
                                "success_rate": 0.0,
                                "detail": "generic failed on 2 of 3 similar subtasks",
                            }
                        ],
                        "metadata": {"code_scope": ["api", "routes.py", "tests"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rankings = store.derive_agent_rankings(cwd=cwd, task="Refactor src/api/routes.py tests")
    context = store.build_context(cwd=cwd, task="Refactor src/api/routes.py tests")

    assert rankings
    assert rankings[0].agent_type == "codex"
    assert rankings[0].reasons == ["codex succeeded on 3 of 3 similar subtasks"]
    assert context is not None
    assert "Recommended agent choices:" in context.text
    assert "Prefer codex" in context.text


def test_memory_store_loads_legacy_entries_without_structured_fields(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")
    workspace_path = store._workspace_path(cwd)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text(
        json.dumps(
            {
                "cwd": cwd,
                "entries": [
                    {
                        "memory_id": "legacy-run",
                        "created_at": "2026-03-01T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Build API routes",
                        "summary": "Codex handled the API route scaffold cleanly.",
                        "insights": ["API routes work stayed in src/api/routes.py"],
                        "completion_pct": 100,
                        "source": "report_completion",
                        "metadata": {
                            "subtasks": [
                                {
                                    "name": "API routes",
                                    "agent_type": "codex",
                                    "status": "done",
                                    "prompt": "Update src/api/routes.py",
                                }
                            ],
                            "code_scope": ["api", "routes.py"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    entries = store.load_entries(cwd)
    context = store.build_context(cwd=cwd, task="Refactor API routes")
    rankings = store.derive_agent_rankings(cwd=cwd, task="Refactor API routes")

    assert entries[0].workspace_facts == []
    assert entries[0].performance_signals == []
    assert context is not None
    assert "Workspace facts:" in context.text
    assert "API routes work stayed in src/api/routes.py" in context.text
    assert rankings
    assert rankings[0].agent_type == "codex"
