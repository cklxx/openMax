from __future__ import annotations

from openmax.memory_system import MemoryStore, infer_code_scope


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
