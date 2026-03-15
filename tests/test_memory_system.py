from __future__ import annotations

import json
from dataclasses import asdict

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


def test_agent_scorecard_aggregates_structured_outcomes_for_similar_work(tmp_path):
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
                        "memory_id": "score-codex-1",
                        "created_at": "2026-03-10T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py tests",
                        "summary": "Structured API route outcomes.",
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
                        "memory_id": "score-codex-2",
                        "created_at": "2026-03-11T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refactor src/api/routes.py handlers",
                        "summary": "More API route outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
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
                        "metadata": {"code_scope": ["api", "routes.py", "handlers"]},
                    },
                    {
                        "memory_id": "score-generic",
                        "created_at": "2026-03-12T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py tests",
                        "summary": "generic drifted on API route retries.",
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
                    {
                        "memory_id": "score-docs",
                        "created_at": "2026-03-12T12:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refresh docs landing page",
                        "summary": "Docs outcomes.",
                        "workspace_facts": ["Relevant scope: docs, index.html"],
                        "agent_stats": [
                            {
                                "agent_type": "claude-code",
                                "success_count": 4,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 4,
                                "success_rate": 1.0,
                                "detail": "claude-code succeeded on 4 of 4 similar subtasks",
                            }
                        ],
                        "metadata": {"code_scope": ["docs", "index.html"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    scorecard = store.derive_agent_scorecard(
        cwd=cwd,
        task="Refactor src/api/routes.py tests",
    )

    assert scorecard
    assert scorecard[0].agent_type == "codex"
    assert scorecard[0].success_count == 5
    assert scorecard[0].total_count == 5
    assert scorecard[0].success_rate == 1.0
    assert scorecard[0].recommendation_score > 0
    assert scorecard[0].reasons[0] == "codex succeeded on 3 of 3 similar subtasks"
    assert any(item.agent_type == "generic" for item in scorecard)
    generic = next(item for item in scorecard if item.agent_type == "generic")
    assert generic.failure_count == 2
    assert generic.incomplete_count == 1
    assert generic.recommendation_score < scorecard[0].recommendation_score


def test_render_workspace_memories_includes_structured_agent_scorecard(tmp_path):
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
                        "memory_id": "score-codex",
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
                        "memory_id": "score-generic",
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

    lines = store.render_workspace_memories(cwd, limit=5)

    assert "Agent scorecard:" in lines
    assert any("codex" in line and "5.0" not in line for line in lines)
    assert any("codex" in line and "3/3" in line for line in lines)
    assert any("generic" in line and "0/3" in line for line in lines)


def test_recommendation_offline_eval_uses_structured_scorecards(tmp_path):
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
                        "memory_id": "docs-seed",
                        "created_at": "2026-03-10T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refresh docs landing page",
                        "summary": "Docs outcomes.",
                        "workspace_facts": ["Relevant scope: docs, index.html"],
                        "agent_stats": [
                            {
                                "agent_type": "claude-code",
                                "success_count": 2,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 2,
                                "success_rate": 1.0,
                                "detail": "claude-code succeeded on 2 of 2 similar subtasks",
                            }
                        ],
                        "completion_pct": 100,
                        "metadata": {"code_scope": ["docs", "index.html"]},
                    },
                    {
                        "memory_id": "api-seed",
                        "created_at": "2026-03-11T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py endpoints",
                        "summary": "API route outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
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
                        "completion_pct": 100,
                        "metadata": {"code_scope": ["api", "routes.py"]},
                    },
                    {
                        "memory_id": "api-handlers",
                        "created_at": "2026-03-12T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refactor src/api/routes.py handlers",
                        "summary": "Handler outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
                        "agent_stats": [
                            {
                                "agent_type": "codex",
                                "success_count": 1,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 1,
                                "success_rate": 1.0,
                                "detail": "codex succeeded on 1 of 1 similar subtasks",
                            },
                            {
                                "agent_type": "generic",
                                "success_count": 0,
                                "failure_count": 1,
                                "incomplete_count": 0,
                                "total_count": 1,
                                "success_rate": 0.0,
                                "detail": "generic failed on 1 of 1 similar subtasks",
                            },
                        ],
                        "completion_pct": 80,
                        "metadata": {"code_scope": ["api", "routes.py", "handlers"]},
                    },
                    {
                        "memory_id": "api-tests",
                        "created_at": "2026-03-13T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Add tests for src/api/routes.py",
                        "summary": "API test outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py, tests"],
                        "agent_stats": [
                            {
                                "agent_type": "codex",
                                "success_count": 1,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 1,
                                "success_rate": 1.0,
                                "detail": "codex succeeded on 1 of 1 similar subtasks",
                            },
                            {
                                "agent_type": "generic",
                                "success_count": 0,
                                "failure_count": 2,
                                "incomplete_count": 0,
                                "total_count": 2,
                                "success_rate": 0.0,
                                "detail": "generic failed on 2 of 2 similar subtasks",
                            },
                        ],
                        "completion_pct": 90,
                        "metadata": {"code_scope": ["api", "routes.py", "tests"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    evaluation = store.evaluate_recommendations_offline(cwd=cwd)

    assert evaluation.total_runs == 4
    assert evaluation.evaluated_runs == 3
    assert evaluation.covered_runs == 2
    assert evaluation.hit_runs == 2
    assert evaluation.coverage == 0.67
    assert evaluation.hit_rate == 1.0
    assert evaluation.average_completion_pct == 85.0
    assert evaluation.average_failure_rate == 0.0


def test_recommendation_eval_report_compares_strategy_to_global_baseline(tmp_path):
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
                        "memory_id": "docs-seed-1",
                        "created_at": "2026-03-09T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refresh docs landing page",
                        "summary": "Docs outcomes.",
                        "workspace_facts": ["Relevant scope: docs, index.html"],
                        "agent_stats": [
                            {
                                "agent_type": "claude-code",
                                "success_count": 4,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 4,
                                "success_rate": 1.0,
                                "detail": "claude-code succeeded on 4 of 4 similar subtasks",
                            }
                        ],
                        "completion_pct": 100,
                        "metadata": {"code_scope": ["docs", "index.html"]},
                    },
                    {
                        "memory_id": "docs-seed-2",
                        "created_at": "2026-03-10T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Polish docs onboarding page",
                        "summary": "More docs outcomes.",
                        "workspace_facts": ["Relevant scope: docs, onboarding.md"],
                        "agent_stats": [
                            {
                                "agent_type": "claude-code",
                                "success_count": 3,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 3,
                                "success_rate": 1.0,
                                "detail": "claude-code succeeded on 3 of 3 similar subtasks",
                            }
                        ],
                        "completion_pct": 100,
                        "metadata": {"code_scope": ["docs", "onboarding.md"]},
                    },
                    {
                        "memory_id": "api-seed",
                        "created_at": "2026-03-11T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Implement src/api/routes.py endpoints",
                        "summary": "API route outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py"],
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
                        "completion_pct": 100,
                        "metadata": {"code_scope": ["api", "routes.py"]},
                    },
                    {
                        "memory_id": "api-handlers",
                        "created_at": "2026-03-12T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Refactor src/api/routes.py handlers",
                        "summary": "Handler outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py, handlers"],
                        "agent_stats": [
                            {
                                "agent_type": "codex",
                                "success_count": 1,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 1,
                                "success_rate": 1.0,
                                "detail": "codex succeeded on 1 of 1 similar subtasks",
                            }
                        ],
                        "completion_pct": 80,
                        "metadata": {"code_scope": ["api", "routes.py", "handlers"]},
                    },
                    {
                        "memory_id": "api-tests",
                        "created_at": "2026-03-13T00:00:00+00:00",
                        "kind": "run_summary",
                        "task": "Add tests for src/api/routes.py",
                        "summary": "API test outcomes.",
                        "workspace_facts": ["Relevant scope: api, routes.py, tests"],
                        "agent_stats": [
                            {
                                "agent_type": "codex",
                                "success_count": 1,
                                "failure_count": 0,
                                "incomplete_count": 0,
                                "total_count": 1,
                                "success_rate": 1.0,
                                "detail": "codex succeeded on 1 of 1 similar subtasks",
                            }
                        ],
                        "completion_pct": 90,
                        "metadata": {"code_scope": ["api", "routes.py", "tests"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = store.evaluate_recommendations_against_baseline(cwd=cwd)

    assert report.strategy.total_runs == 5
    assert report.strategy.evaluated_runs == 4
    assert report.strategy.covered_runs == 3
    assert report.strategy.hit_runs == 3
    assert report.strategy.hit_rate == 1.0
    assert report.strategy.average_failure_rate == 0.0
    assert report.baseline.total_runs == 5
    assert report.baseline.evaluated_runs == 4
    assert report.baseline.covered_runs == 4
    assert report.baseline.hit_runs == 1
    assert report.baseline.hit_rate == 0.25
    assert report.baseline.average_failure_rate == 0.75
    assert report.hit_rate_lift == 0.75
    assert report.failure_rate_delta == -0.75


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
