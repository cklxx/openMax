"""Tests for openmax.clean — workspace and session cleanup."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=path,
        capture_output=True,
        check=True,
    )


def _create_branch(path: Path, name: str) -> None:
    subprocess.run(["git", "branch", name], cwd=path, capture_output=True, check=True)


def _list_branches(path: Path) -> list[str]:
    r = subprocess.run(
        ["git", "branch", "--list", "openmax/*"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return [b.strip() for b in r.stdout.splitlines() if b.strip()]


class TestCleanWorkspace:
    def test_removes_branches_and_verifies_gone(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        _create_branch(tmp_path, "openmax/task-a")
        _create_branch(tmp_path, "openmax/task-b")

        from openmax.clean import clean_workspace

        report = clean_workspace(str(tmp_path))
        assert len(report.branches_removed) == 2
        assert _list_branches(tmp_path) == []

    def test_full_cleanup_with_all_artifact_types(self, tmp_path: Path) -> None:
        """Integration: create every artifact type, clean, verify all gone."""
        _init_git_repo(tmp_path)
        _create_branch(tmp_path, "openmax/x")
        (tmp_path / ".openmax-worktrees" / "openmax_x").mkdir(parents=True)
        for subdir in ("briefs", "reports", "shared", "checkpoints"):
            d = tmp_path / ".openmax" / subdir
            d.mkdir(parents=True)
            (d / "file.md").write_text("data")
        (tmp_path / ".openmax" / "messages-s1.jsonl").write_text("{}")

        from openmax.clean import clean_workspace

        report = clean_workspace(str(tmp_path))
        assert report.total_removed > 0
        assert _list_branches(tmp_path) == []
        assert not (tmp_path / ".openmax-worktrees").exists()
        for subdir in ("briefs", "reports", "shared", "checkpoints"):
            assert not (tmp_path / ".openmax" / subdir).exists()

    def test_preserves_gitignore(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        gi = tmp_path / ".openmax" / ".gitignore"
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text("*\n")
        (tmp_path / ".openmax" / "briefs").mkdir()
        (tmp_path / ".openmax" / "briefs" / "x.md").write_text("y")

        from openmax.clean import clean_workspace

        clean_workspace(str(tmp_path))
        assert gi.exists()


class TestExpireSessions:
    def test_expires_old_keeps_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmax import clean

        monkeypatch.setattr(clean, "_sessions_dir", lambda: tmp_path)
        monkeypatch.setattr(clean, "_SESSION_KEEP_MIN", 1)

        for i, age_days in enumerate([60, 45, 1]):
            session_dir = tmp_path / f"hash{i}" / f"session-{i}"
            session_dir.mkdir(parents=True)
            (session_dir / "meta.json").write_text(json.dumps({"id": i}))
            mtime = time.time() - age_days * 86400
            os.utime(session_dir, (mtime, mtime))

        removed = clean.expire_old_sessions(max_age_days=30)
        assert removed == 2
        assert (tmp_path / "hash2" / "session-2").exists()

    def test_keeps_minimum_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmax import clean

        monkeypatch.setattr(clean, "_sessions_dir", lambda: tmp_path)
        monkeypatch.setattr(clean, "_SESSION_KEEP_MIN", 5)

        for i in range(3):
            session_dir = tmp_path / f"hash{i}" / f"session-{i}"
            session_dir.mkdir(parents=True)
            (session_dir / "meta.json").write_text("{}")
            mtime = time.time() - 60 * 86400
            os.utime(session_dir, (mtime, mtime))

        assert clean.expire_old_sessions(max_age_days=30) == 0


class TestCleanCLI:
    def test_dry_run_does_not_remove(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        _create_branch(tmp_path, "openmax/test-branch")

        from click.testing import CliRunner

        from openmax.cli import main

        result = CliRunner().invoke(main, ["clean", "--dry-run", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert "openmax/test-branch" in _list_branches(tmp_path)

    def test_actual_clean_removes_artifacts(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        _create_branch(tmp_path, "openmax/test-branch")

        from click.testing import CliRunner

        from openmax.cli import main

        result = CliRunner().invoke(main, ["clean", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert _list_branches(tmp_path) == []
