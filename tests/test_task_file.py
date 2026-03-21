from __future__ import annotations

from openmax.task_file import (
    append_shared_context,
    delete_checkpoint,
    inject_claude_md,
    list_checkpoint_paths,
    read_checkpoint,
    read_shared_context,
    write_checkpoint,
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
