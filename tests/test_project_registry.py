"""Tests for project registry."""

from __future__ import annotations

import subprocess
from pathlib import Path

from openmax.project_registry import (
    add_project,
    find_project,
    list_projects,
    remove_project,
    status_all,
)


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=str(path),
        check=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def test_add_and_list(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)

    name, err = add_project(str(repo))
    assert err is None
    assert name == "myrepo"

    projects = list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "myrepo"


def test_add_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)

    add_project(str(repo))
    name, err = add_project(str(repo))
    assert err is not None
    assert "Already registered" in err


def test_add_nonexistent_path(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    _, err = add_project(str(tmp_path / "nope"))
    assert err is not None
    assert "does not exist" in err


def test_add_non_git_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    plain = tmp_path / "plain"
    plain.mkdir()
    _, err = add_project(str(plain))
    assert err is not None
    assert "Not a git" in err


def test_remove(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)

    add_project(str(repo))
    err = remove_project("myrepo")
    assert err is None
    assert list_projects() == []


def test_remove_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    err = remove_project("nope")
    assert err is not None
    assert "not found" in err


def test_find_project(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)
    add_project(str(repo))

    assert find_project("myrepo") == str(repo.resolve())
    assert find_project("nope") is None


def test_status_all(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)
    add_project(str(repo))

    results = status_all()
    assert len(results) == 1
    assert results[0]["status"] == "clean"
    assert results[0]["branch"] == "main"


def test_status_missing_path(monkeypatch, tmp_path):
    monkeypatch.setattr("openmax.project_registry._REGISTRY_PATH", tmp_path / "projects.yaml")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)
    add_project(str(repo))

    # Delete the repo
    import shutil

    shutil.rmtree(repo)

    results = status_all()
    assert results[0]["status"] == "missing"
