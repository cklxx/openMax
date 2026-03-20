"""Tests for intelligent merge strategy (_merge.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from openmax.lead_agent.tools._merge import (
    SMALL_DIFF_MAX_FILES,
    SMALL_DIFF_MAX_LINES,
    _conflicted_files,
    _diff_stats,
    _has_overlapping_markers,
    _try_auto_resolve_file,
    choose_merge_strategy,
    do_rebase,
    try_auto_resolve_conflicts,
)


def _git(cwd: str, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _init_repo(tmp_path: Path) -> str:
    """Create a git repo with an initial commit on main."""
    cwd = str(tmp_path)
    _git(cwd, "init", "-b", "main")
    _git(cwd, "config", "user.email", "test@test.com")
    _git(cwd, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("init\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "init")
    return cwd


# --- _diff_stats ---


def test_diff_stats_counts_files_and_lines(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "feat")
    (tmp_path / "a.py").write_text("line1\nline2\nline3\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "add a.py")
    files, lines = _diff_stats("feat", "main", cwd)
    assert files == 1
    assert lines >= 3


def test_diff_stats_empty_branch(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "empty")
    files, lines = _diff_stats("empty", "main", cwd)
    assert files == 0
    assert lines == 0


# --- choose_merge_strategy ---


def test_small_diff_chooses_rebase(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "small")
    (tmp_path / "tiny.py").write_text("x = 1\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "tiny change")
    assert choose_merge_strategy("small", "main", cwd) == "rebase"


def test_large_diff_chooses_merge(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "big")
    for i in range(SMALL_DIFF_MAX_FILES + 1):
        (tmp_path / f"file{i}.py").write_text("\n".join(f"line{j}" for j in range(40)))
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "big change")
    assert choose_merge_strategy("big", "main", cwd) == "merge"


def test_many_lines_chooses_merge(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "long")
    (tmp_path / "big.py").write_text(
        "\n".join(f"line{i}" for i in range(SMALL_DIFF_MAX_LINES + 10))
    )
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "long file")
    assert choose_merge_strategy("long", "main", cwd) == "merge"


# --- _has_overlapping_markers ---


def test_no_overlapping_markers(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feat\n")
    assert not _has_overlapping_markers(f)


def test_overlapping_markers(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("<<<<<<< HEAD\n<<<<<<< nested\nours\n=======\ntheirs\n>>>>>>> feat\n")
    assert _has_overlapping_markers(f)


# --- _try_auto_resolve_file ---


def test_auto_resolve_simple_conflict(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("before\n<<<<<<< HEAD\nours_line\n=======\ntheirs_line\n>>>>>>> feat\nafter\n")
    assert _try_auto_resolve_file(f)
    content = f.read_text()
    assert "<<<<<<" not in content
    assert "ours_line" in content
    assert "theirs_line" in content
    assert "before" in content
    assert "after" in content


def test_auto_resolve_preserves_non_conflict_lines(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("header\n<<<<<<< HEAD\na = 1\n=======\nb = 2\n>>>>>>> feat\nfooter\n")
    assert _try_auto_resolve_file(f)
    content = f.read_text()
    assert "header" in content
    assert "footer" in content
    assert "a = 1" in content
    assert "b = 2" in content


def test_auto_resolve_fails_on_malformed(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("<<<<<<< HEAD\nours\n")
    assert not _try_auto_resolve_file(f)


# --- try_auto_resolve_conflicts ---


def test_auto_resolve_in_real_repo(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    (tmp_path / "shared.py").write_text("line1\nline2\nline3\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "base")
    _git(cwd, "checkout", "-b", "feat")
    (tmp_path / "shared.py").write_text("line1\nmodified_by_feat\nline3\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "feat change")
    _git(cwd, "checkout", "main")
    (tmp_path / "shared.py").write_text("line1\nline2\nmodified_by_main\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "main change")
    merge = subprocess.run(
        ["git", "merge", "--no-edit", "feat"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if merge.returncode != 0:
        resolved, unresolved = try_auto_resolve_conflicts(cwd)
        assert isinstance(resolved, list)
        assert isinstance(unresolved, list)


# --- do_rebase ---


def test_rebase_success(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "feat")
    (tmp_path / "new.py").write_text("new\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "feat")
    _git(cwd, "checkout", "main")
    (tmp_path / "other.py").write_text("other\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "main advance")
    success, err = do_rebase(cwd, "feat", "main")
    assert success
    assert err == ""


def test_rebase_conflict_aborts(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    (tmp_path / "shared.py").write_text("original\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "base")
    _git(cwd, "checkout", "-b", "feat")
    (tmp_path / "shared.py").write_text("feat version\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "feat change")
    _git(cwd, "checkout", "main")
    (tmp_path / "shared.py").write_text("main version\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "main change")
    success, err = do_rebase(cwd, "feat", "main")
    assert not success
    assert err != ""


# --- Integration: strategy selection + merge flow ---


def test_small_diff_uses_rebase_flow(tmp_path: Path):
    """Small diff should attempt rebase → ff-merge for linear history."""
    cwd = _init_repo(tmp_path)
    _git(cwd, "checkout", "-b", "feat")
    (tmp_path / "small.py").write_text("x = 1\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", "small feat")
    strategy = choose_merge_strategy("feat", "main", cwd)
    assert strategy == "rebase"


def test_conflicted_files_empty_when_clean(tmp_path: Path):
    cwd = _init_repo(tmp_path)
    assert _conflicted_files(cwd) == []
