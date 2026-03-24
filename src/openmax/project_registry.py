"""Project registry — manage registered projects in ~/.openmax/projects.yaml."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

_REGISTRY_PATH = Path.home() / ".openmax" / "projects.yaml"


def _load() -> list[dict[str, str]]:
    try:
        data = yaml.safe_load(_REGISTRY_PATH.read_text()) or {}
        return data.get("projects", [])
    except (FileNotFoundError, yaml.YAMLError):
        return []


def _save(projects: list[dict[str, str]]) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(yaml.dump({"projects": projects}, default_flow_style=False))


def _detect_name(path: Path) -> str:
    """Auto-detect project name from git remote or directory name."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            url = r.stdout.strip().rstrip("/")
            return url.rsplit("/", 1)[-1].removesuffix(".git")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return path.name


def _is_git_repo(path: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(path),
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def add_project(path: str) -> tuple[str, str | None]:
    """Register a project. Returns (name, error_or_None)."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        return "", f"Path does not exist: {resolved}"
    if not _is_git_repo(resolved):
        return "", f"Not a git repository: {resolved}"
    projects = _load()
    for p in projects:
        if Path(p["path"]).resolve() == resolved:
            return p["name"], f"Already registered as '{p['name']}'"
    name = _detect_name(resolved)
    projects.append({"name": name, "path": str(resolved)})
    _save(projects)
    return name, None


def remove_project(name: str) -> str | None:
    """Remove a project by name. Returns error or None."""
    projects = _load()
    filtered = [p for p in projects if p["name"] != name]
    if len(filtered) == len(projects):
        return f"Project '{name}' not found"
    _save(filtered)
    return None


def list_projects() -> list[dict[str, str]]:
    """Return all registered projects."""
    return _load()


def find_project(name: str) -> str | None:
    """Find project path by name. Returns path or None."""
    for p in _load():
        if p["name"] == name:
            return p["path"]
    return None


def status_all() -> list[dict[str, str]]:
    """Return git status for all registered projects."""
    results = []
    for p in _load():
        path = Path(p["path"])
        entry = {"name": p["name"], "path": p["path"]}
        if not path.is_dir():
            entry["status"] = "missing"
            results.append(entry)
            continue
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            entry["branch"] = branch.stdout.strip() if branch.returncode == 0 else "?"
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            dirty = len([ln for ln in status.stdout.splitlines() if ln.strip()])
            entry["status"] = f"{dirty} dirty" if dirty else "clean"
        except (OSError, subprocess.TimeoutExpired):
            entry["status"] = "error"
        results.append(entry)
    return results
