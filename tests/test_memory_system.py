from __future__ import annotations

from openmax.memory_system import MemoryStore


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
