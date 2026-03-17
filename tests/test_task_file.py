from __future__ import annotations

from openmax.task_file import (
    append_shared_context,
    delete_checkpoint,
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
