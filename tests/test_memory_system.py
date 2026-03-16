from __future__ import annotations

import json
from dataclasses import asdict

from openmax.memory import (
    MemoryEntry,
    MemoryStore,
    classify_task,
    infer_code_scope,
    predict_next_queries,
)


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
    assert "Prior workspace learnings (use these to guide decisions):" in context.text
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


# ── Predictive memory: classify_task ──────────────────────────────


def test_classify_task_identifies_code_category():
    assert classify_task("Implement new user endpoint") == "code"
    assert classify_task("Write the login handler") == "code"
    assert classify_task("Create a new module for auth") == "code"


def test_classify_task_identifies_testing():
    assert classify_task("Run pytest tests with full coverage") == "testing"
    assert classify_task("Add pytest coverage for auth module") == "testing"


def test_classify_task_identifies_debugging():
    assert classify_task("Fix the crash in payment handler") == "debugging"
    assert classify_task("Debug the auth error on login") == "debugging"


def test_classify_task_identifies_refactor():
    assert classify_task("Refactor the service layer and simplify") == "refactor"
    assert classify_task("Rename and reorganize utility functions") == "refactor"


def test_classify_task_identifies_architecture():
    assert classify_task("Design the new database schema") == "architecture"
    assert classify_task("Set up the CI pipeline for deploys") == "architecture"


def test_classify_task_identifies_docs():
    assert classify_task("Update the README with new API docs") == "docs"
    assert classify_task("Add documentation and docstring comments") == "docs"


def test_classify_task_defaults_to_code_for_ambiguous_input():
    assert classify_task("do something with the project") == "code"
    assert classify_task("") == "code"


# ── Predictive memory: predict_next_queries ───────────────────────


def test_predict_next_queries_for_successful_code_task():
    preds = predict_next_queries(
        "Implement src/api/routes.py endpoints",
        completion_pct=100,
        subtasks=[{"name": "API routes", "agent_type": "codex", "status": "done"}],
    )
    assert len(preds) >= 1
    # Should suggest testing or review as follow-up
    combined = " ".join(preds).lower()
    assert "test" in combined or "review" in combined or "refactor" in combined


def test_predict_next_queries_for_partial_task_suggests_continue():
    preds = predict_next_queries(
        "Implement user authentication",
        completion_pct=40,
        subtasks=[{"name": "Auth module", "agent_type": "codex", "status": "pending"}],
    )
    assert len(preds) >= 1
    # Should include a continuation suggestion
    assert any("continue" in p.lower() for p in preds)


def test_predict_next_queries_for_debugging_success():
    preds = predict_next_queries(
        "Fix the crash in payment handler",
        completion_pct=100,
        subtasks=[{"name": "Payment fix", "agent_type": "claude-code", "status": "done"}],
    )
    combined = " ".join(preds).lower()
    assert "regression" in combined or "test" in combined or "refactor" in combined


def test_predict_next_queries_for_testing_partial():
    preds = predict_next_queries(
        "Write tests for the auth module",
        completion_pct=50,
    )
    assert any("continue" in p.lower() or "fix" in p.lower() for p in preds)


# ── Predictive memory: session-end prediction storage ─────────────


def test_run_summary_stores_predictions_and_category(tmp_path):
    """Session-end prediction: verify predictions are persisted in metadata."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    entry = store.record_run_summary(
        cwd=cwd,
        task="Implement src/api/routes.py endpoints",
        notes="Successfully built all API endpoints.",
        completion_pct=100,
        subtasks=[
            {"name": "API routes", "agent_type": "codex", "status": "done"},
        ],
        anchors=[{"summary": "API endpoints implemented."}],
    )

    assert "predictions" in entry.metadata
    assert isinstance(entry.metadata["predictions"], list)
    assert len(entry.metadata["predictions"]) >= 1
    assert "task_category" in entry.metadata
    assert entry.metadata["task_category"] == "code"

    # Verify predictions survive round-trip through persistence
    loaded = store.load_entries(cwd)
    assert loaded[-1].metadata["predictions"] == entry.metadata["predictions"]


def test_lesson_stores_task_category(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    entry = store.record_lesson(
        cwd=cwd,
        task="Fix the auth bug",
        lesson="Always check token expiry before refresh.",
        confidence=9,
    )
    assert entry.metadata.get("task_category") == "debugging"


# ── Predictive memory: query distribution ─────────────────────────


def test_query_distribution_tracks_task_categories(tmp_path):
    """Query distribution: verify category counts accumulate correctly."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # Session 1: code task
    store.record_run_summary(
        cwd=cwd,
        task="Implement user login endpoint",
        notes="Login endpoint done.",
        completion_pct=100,
        subtasks=[{"name": "Login", "agent_type": "codex", "status": "done"}],
        anchors=[],
    )
    # Session 2: another code task
    store.record_run_summary(
        cwd=cwd,
        task="Create user registration handler",
        notes="Registration handler done.",
        completion_pct=100,
        subtasks=[{"name": "Register", "agent_type": "codex", "status": "done"}],
        anchors=[],
    )
    # Session 3: testing task
    store.record_run_summary(
        cwd=cwd,
        task="Run pytest tests and assert coverage for user login",
        notes="Tests written.",
        completion_pct=100,
        subtasks=[{"name": "Login tests", "agent_type": "claude-code", "status": "done"}],
        anchors=[],
    )
    # Session 4: debugging task
    store.record_run_summary(
        cwd=cwd,
        task="Fix the auth bug in registration",
        notes="Bug fixed.",
        completion_pct=100,
        subtasks=[{"name": "Auth fix", "agent_type": "claude-code", "status": "done"}],
        anchors=[],
    )

    dist = store.load_query_distribution(cwd)

    assert "code" in dist
    assert "testing" in dist
    assert "debugging" in dist
    # code had 2 out of 4 runs
    assert abs(dist["code"] - 0.5) < 0.01
    # testing had 1 out of 4
    assert abs(dist["testing"] - 0.25) < 0.01
    # debugging had 1 out of 4
    assert abs(dist["debugging"] - 0.25) < 0.01


def test_query_distribution_empty_for_new_workspace(tmp_path):
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "new-workspace")
    dist = store.load_query_distribution(cwd)
    assert dist == {}


# ── Predictive memory: dual-buffer context ────────────────────────


def test_dual_buffer_separates_active_and_predictive_entries(tmp_path):
    """Dual-buffer: active entries are keyword-matched, predictive are prediction-matched.

    Scenario: User did 3 sessions in a coding workspace:
      1. Implemented API routes (code, 100%)
      2. Built a dashboard component (code, 100%)
      3. Fixed an auth bug (debugging, 100%)

    Now user asks: "Write tests for the API routes"
    - Active buffer should pick up session 1 (keyword match: "API routes")
    - Predictive buffer should pick up session 1's prediction ("write tests for...")
    """
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # Session 1: implemented API routes
    store.record_run_summary(
        cwd=cwd,
        task="Implement API routes for user service",
        notes="All routes implemented successfully.",
        completion_pct=100,
        subtasks=[{"name": "API routes", "agent_type": "codex", "status": "done"}],
        anchors=[{"summary": "User service API routes done."}],
    )
    # Session 2: built dashboard
    store.record_run_summary(
        cwd=cwd,
        task="Create dashboard component with charts",
        notes="Dashboard component built.",
        completion_pct=100,
        subtasks=[{"name": "Dashboard", "agent_type": "claude-code", "status": "done"}],
        anchors=[{"summary": "Dashboard charts implemented."}],
    )
    # Session 3: fixed auth bug
    store.record_run_summary(
        cwd=cwd,
        task="Fix authentication token refresh bug",
        notes="Token refresh bug fixed.",
        completion_pct=100,
        subtasks=[{"name": "Auth fix", "agent_type": "claude-code", "status": "done"}],
        anchors=[{"summary": "Token refresh now works correctly."}],
    )

    # New query: "Write tests for the API routes"
    context = store.build_context(cwd=cwd, task="Write tests for the API routes", limit=4)

    assert context is not None
    assert context.matched_entries >= 1
    assert context.active_entries >= 0
    assert context.active_entries + context.predictive_entries == context.matched_entries
    assert "Prior workspace learnings (use these to guide decisions):" in context.text
    lower_text = context.text.lower()
    assert "api routes" in lower_text or "routes" in lower_text


def test_dual_buffer_with_distribution_boost(tmp_path):
    """Dual-buffer: entries from high-frequency categories get distribution boost.

    Scenario: In a workspace where 80% of work is code-related, a code-related
    entry should score higher in the predictive buffer than a docs entry.
    """
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # Build up distribution: 4 code tasks, 1 docs task
    for i in range(4):
        store.record_run_summary(
            cwd=cwd,
            task=f"Implement feature {i} in src/api/handlers.py",
            notes=f"Feature {i} done.",
            completion_pct=100,
            subtasks=[{"name": f"Feature {i}", "agent_type": "codex", "status": "done"}],
            anchors=[],
        )
    store.record_run_summary(
        cwd=cwd,
        task="Write documentation for onboarding guide",
        notes="Guide written.",
        completion_pct=100,
        subtasks=[{"name": "Docs", "agent_type": "claude-code", "status": "done"}],
        anchors=[],
    )

    dist = store.load_query_distribution(cwd)
    assert dist["code"] > dist.get("docs", 0)

    # Query a code task — code entries should be boosted
    context = store.build_context(cwd=cwd, task="Build a new endpoint for payments", limit=4)
    assert context is not None
    assert "Task distribution:" in context.text
    assert "code" in context.text.lower()


def test_dual_buffer_predictive_catches_orthogonal_query(tmp_path):
    """Dual-buffer: prediction matches catch queries with no keyword overlap.

    This is the key case: the active buffer won't match because "write tests"
    doesn't overlap with "implement payment service". But the predictive buffer
    matches because the stored prediction "write tests for..." overlaps.
    """
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # Session 1: implement payment service — predictions should include "write tests"
    store.record_run_summary(
        cwd=cwd,
        task="Implement payment service integration",
        notes="Payment service integrated.",
        completion_pct=100,
        subtasks=[{"name": "Payment integration", "agent_type": "codex", "status": "done"}],
        anchors=[{"summary": "Stripe payment integration complete."}],
    )

    # Verify predictions were stored
    entries = store.load_entries(cwd)
    preds = entries[-1].metadata.get("predictions", [])
    assert any("test" in p.lower() for p in preds), f"Expected test prediction, got: {preds}"

    # Now user asks to write tests — no keyword overlap with "payment service"
    context = store.build_context(cwd=cwd, task="Write comprehensive test suite", limit=4)
    assert context is not None
    assert context.matched_entries >= 1


def test_dual_buffer_fallback_when_no_matches(tmp_path):
    """Dual-buffer: falls back to most recent entries when both buffers are empty."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    store.record_lesson(
        cwd=cwd,
        task="Miscellaneous cleanup",
        lesson="Always run lint before committing.",
        confidence=7,
    )

    # Query with zero keyword overlap
    context = store.build_context(cwd=cwd, task="Something completely different XYZ")
    assert context is not None
    assert context.matched_entries >= 1
    assert "Always run lint before committing." in context.text


# ── Predictive memory: multi-session end-to-end scenario ──────────


def test_multi_session_workflow_e2e(tmp_path):
    """End-to-end: simulate a realistic 5-session development workflow.

    Session 1: User implements API routes → predictions: write tests, review code
    Session 2: User writes tests (predicted!) → predictions: improve coverage
    Session 3: User finds and fixes a bug → predictions: add regression tests
    Session 4: User adds regression tests (predicted!)
    Session 5: User refactors the code → verify full distribution and context
    """
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # ── Session 1: Implement API routes ───────────────────────────
    s1 = store.record_run_summary(
        cwd=cwd,
        task="Implement src/api/routes.py REST endpoints",
        notes="All REST endpoints for user service implemented.",
        completion_pct=100,
        subtasks=[
            {"name": "GET /users", "agent_type": "codex", "status": "done"},
            {"name": "POST /users", "agent_type": "codex", "status": "done"},
            {"name": "DELETE /users/:id", "agent_type": "codex", "status": "done"},
        ],
        anchors=[{"summary": "REST API endpoints for user service."}],
    )
    s1_preds = s1.metadata["predictions"]
    assert len(s1_preds) >= 1
    assert s1.metadata["task_category"] == "code"

    # ── Session 2: Write tests (follows prediction from Session 1) ─
    s2 = store.record_run_summary(
        cwd=cwd,
        task="Write pytest tests for src/api/routes.py",
        notes="Full test coverage for user API routes.",
        completion_pct=100,
        subtasks=[
            {"name": "Test GET /users", "agent_type": "claude-code", "status": "done"},
            {"name": "Test POST /users", "agent_type": "claude-code", "status": "done"},
        ],
        anchors=[{"summary": "API route tests passing."}],
    )
    assert s2.metadata["task_category"] == "testing"

    # ── Session 3: Debug a bug discovered during testing ──────────
    store.record_lesson(
        cwd=cwd,
        task="Fix race condition in user creation",
        lesson="Use database transaction for user creation to avoid race conditions.",
        rationale="Concurrent POST /users caused duplicate entries.",
        confidence=9,
    )
    s3 = store.record_run_summary(
        cwd=cwd,
        task="Fix and debug race condition bug in user creation",
        notes="Race condition fixed with transaction lock.",
        completion_pct=100,
        subtasks=[
            {"name": "Fix race condition", "agent_type": "claude-code", "status": "done"},
        ],
        anchors=[{"summary": "Transaction lock added to POST /users."}],
    )
    assert s3.metadata["task_category"] == "debugging"
    s3_preds = s3.metadata["predictions"]
    assert any("test" in p.lower() or "regression" in p.lower() for p in s3_preds)

    # ── Session 4: Add regression tests (follows prediction) ──────
    store.record_run_summary(
        cwd=cwd,
        task="Add regression tests for race condition fix",
        notes="Regression tests added for concurrent user creation.",
        completion_pct=100,
        subtasks=[
            {"name": "Regression test", "agent_type": "claude-code", "status": "done"},
        ],
        anchors=[{"summary": "Regression tests for race condition."}],
    )

    # ── Session 5: Refactor — verify full context ─────────────────
    context = store.build_context(
        cwd=cwd,
        task="Refactor src/api/routes.py for better error handling",
        limit=4,
    )

    assert context is not None
    assert context.matched_entries >= 1
    assert context.active_entries + context.predictive_entries == context.matched_entries

    # Check distribution reflects the actual work done
    dist = store.load_query_distribution(cwd)
    assert "code" in dist
    assert "testing" in dist
    assert "debugging" in dist
    total_dist = sum(dist.values())
    assert abs(total_dist - 1.0) < 0.01

    # Context should include the lesson about race conditions
    lower_text = context.text.lower()
    assert "race condition" in lower_text or "transaction" in lower_text

    # Distribution line should appear
    assert "Task distribution:" in context.text


def test_predictions_text_appears_in_context_when_used(tmp_path):
    """Verify MemoryContext.predictions_used is populated when predictions match."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    # Record a code run — its predictions should include "write tests"
    store.record_run_summary(
        cwd=cwd,
        task="Implement new feature in src/core/engine.py",
        notes="Engine feature implemented.",
        completion_pct=100,
        subtasks=[{"name": "Engine feature", "agent_type": "codex", "status": "done"}],
        anchors=[],
    )

    # Query that should match the prediction
    context = store.build_context(cwd=cwd, task="Write tests and review the code", limit=4)
    assert context is not None
    if context.predictive_entries > 0:
        assert len(context.predictions_used) >= 1


def test_partial_completion_generates_continuation_prediction(tmp_path):
    """A partially-completed run should predict continuation as the top follow-up."""
    store = MemoryStore(base_dir=tmp_path)
    cwd = str(tmp_path / "workspace")

    entry = store.record_run_summary(
        cwd=cwd,
        task="Implement full authentication system",
        notes="Only login endpoint done, registration still pending.",
        completion_pct=40,
        subtasks=[
            {"name": "Login endpoint", "agent_type": "codex", "status": "done"},
            {"name": "Registration endpoint", "agent_type": "codex", "status": "pending"},
        ],
        anchors=[],
    )

    preds = entry.metadata["predictions"]
    assert "continue" in preds[0].lower()

    # When user comes back, context should surface this
    context = store.build_context(
        cwd=cwd,
        task="Continue implementing the authentication system",
        limit=4,
    )
    assert context is not None
    assert context.matched_entries >= 1
