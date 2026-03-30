from __future__ import annotations

from openmax.task_file import (
    append_shared_context,
    contract_path,
    delete_checkpoint,
    evaluation_path,
    inject_claude_md,
    list_checkpoint_paths,
    read_checkpoint,
    read_evaluation,
    read_shared_context,
    read_spec,
    spec_path,
    write_checkpoint,
    write_contract,
    write_spec,
)


def test_append_and_read_shared_context(tmp_path):
    append_shared_context(str(tmp_path), "First entry", section="Init")
    append_shared_context(str(tmp_path), "Second entry", section="Update")
    content = read_shared_context(str(tmp_path))
    assert content is not None
    assert "Init" in content
    assert "First entry" in content
    assert "Update" in content
    assert "Second entry" in content


def test_checkpoint_lifecycle(tmp_path):
    write_checkpoint(str(tmp_path), "my-task", "## Decision needed\nPick A or B")
    content = read_checkpoint(str(tmp_path), "my-task")
    assert content is not None
    assert "Pick A or B" in content
    delete_checkpoint(str(tmp_path), "my-task")
    assert read_checkpoint(str(tmp_path), "my-task") is None


def test_list_checkpoint_paths_empty(tmp_path):
    paths = list_checkpoint_paths(str(tmp_path))
    assert paths == []


def test_inject_claude_md_without_session_id(tmp_path):
    inject_claude_md(str(tmp_path), "my-task")
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "openMax Task: my-task" in content
    assert "report_done" not in content


def test_inject_claude_md_with_session_id(tmp_path):
    inject_claude_md(str(tmp_path), "my-task", session_id="sess-123")
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "openMax Task: my-task" in content
    assert 'report_done(task="my-task"' in content
    assert 'session_id="sess-123"' in content
    assert 'report_progress(task="my-task"' in content


def test_inject_claude_md_appends_to_existing(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")
    inject_claude_md(str(tmp_path), "my-task", session_id="sess-456")
    content = (tmp_path / "CLAUDE.md").read_text()
    assert content.startswith("# Existing\n")
    assert "openMax Task: my-task" in content


def test_inject_claude_md_skips_if_already_injected(tmp_path):
    inject_claude_md(str(tmp_path), "first-task")
    first_content = (tmp_path / "CLAUDE.md").read_text()
    inject_claude_md(str(tmp_path), "second-task", session_id="sess-789")
    assert (tmp_path / "CLAUDE.md").read_text() == first_content


# ---------------------------------------------------------------------------
# Harness file helpers
# ---------------------------------------------------------------------------


def test_spec_write_and_read(tmp_path):
    write_spec(str(tmp_path), "# Product Spec\nBuild a todo app")
    content = read_spec(str(tmp_path))
    assert content is not None
    assert "todo app" in content


def test_read_spec_returns_none_when_missing(tmp_path):
    assert read_spec(str(tmp_path)) is None


def test_spec_path_structure(tmp_path):
    path = spec_path(str(tmp_path))
    assert path.name == "spec.md"
    assert "specs" in str(path)


def test_contract_write_and_read(tmp_path):
    write_contract(str(tmp_path), "frontend", 1, "# Sprint Contract")
    path = contract_path(str(tmp_path), "frontend", 1)
    assert path.exists()
    assert "Sprint Contract" in path.read_text()


def test_evaluation_path_structure(tmp_path):
    path = evaluation_path(str(tmp_path), "task", 2)
    assert "task-round-2.md" in str(path)


def test_read_evaluation_returns_none_when_missing(tmp_path):
    assert read_evaluation(str(tmp_path), "nonexistent", 1) is None


def test_read_evaluation_returns_content(tmp_path):
    path = evaluation_path(str(tmp_path), "task", 1)
    path.parent.mkdir(parents=True)
    path.write_text("## Design Quality\nScore: 8/10")
    content = read_evaluation(str(tmp_path), "task", 1)
    assert "8/10" in content
