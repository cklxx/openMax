"""Auto-detect project lint, format, and test commands from config files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectTooling:
    """Detected lint and test commands for a project."""

    lint_cmd: str | None = None
    test_cmd: str | None = None
    language: str | None = None


_PYTHON_LINTERS = {
    "ruff.toml": "ruff check . && ruff format --check .",
    ".flake8": "flake8 .",
}

_JS_LINTERS = {
    ".eslintrc": "npx eslint .",
    ".eslintrc.js": "npx eslint .",
    ".eslintrc.json": "npx eslint .",
    ".eslintrc.yml": "npx eslint .",
    "eslint.config.js": "npx eslint .",
    "eslint.config.mjs": "npx eslint .",
    "eslint.config.ts": "npx eslint .",
    "biome.json": "npx biome check .",
    "biome.jsonc": "npx biome check .",
}


def _detect_python(root: Path) -> ProjectTooling | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() and not (root / "setup.py").exists():
        return None

    lint_cmd = None
    test_cmd = None

    # Check pyproject.toml for tool config
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            if "[tool.ruff" in text:
                lint_cmd = "ruff check . && ruff format --check ."
            if "[tool.pytest" in text or "[tool.pytest.ini_options]" in text:
                test_cmd = "pytest"
            if "[tool.mypy" in text and lint_cmd:
                lint_cmd += " && mypy ."
        except OSError:
            pass

    # Standalone linter configs
    if lint_cmd is None:
        for filename, cmd in _PYTHON_LINTERS.items():
            if (root / filename).exists():
                lint_cmd = cmd
                break

    # Test runner fallback
    if test_cmd is None:
        if (root / "pytest.ini").exists() or (root / "setup.cfg").exists():
            test_cmd = "pytest"
        elif (root / "tests").is_dir() or (root / "test").is_dir():
            test_cmd = "pytest"

    if lint_cmd is None and test_cmd is None:
        return None
    return ProjectTooling(lint_cmd=lint_cmd, test_cmd=test_cmd, language="python")


def _detect_javascript(root: Path) -> ProjectTooling | None:
    pkg_json = root / "package.json"
    if not pkg_json.exists():
        return None

    lint_cmd = None
    test_cmd = None

    # Check package.json scripts
    try:
        import json

        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})
        if "lint" in scripts:
            lint_cmd = "npm run lint"
        if "test" in scripts:
            test_cmd = "npm run test"
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    # Standalone linter configs
    if lint_cmd is None:
        for filename, cmd in _JS_LINTERS.items():
            if (root / filename).exists():
                lint_cmd = cmd
                break

    # Prettier as format check
    if (root / ".prettierrc").exists() or (root / ".prettierrc.json").exists():
        prettier_cmd = "npx prettier --check ."
        lint_cmd = f"{lint_cmd} && {prettier_cmd}" if lint_cmd else prettier_cmd

    if lint_cmd is None and test_cmd is None:
        return None
    lang = "typescript" if (root / "tsconfig.json").exists() else "javascript"
    return ProjectTooling(lint_cmd=lint_cmd, test_cmd=test_cmd, language=lang)


def _detect_go(root: Path) -> ProjectTooling | None:
    if not (root / "go.mod").exists():
        return None
    lint_cmd = "go vet ./..."
    if _any_exists(root, "golangci-lint.yml", ".golangci.yml", ".golangci.yaml"):
        lint_cmd = "golangci-lint run"
    return ProjectTooling(lint_cmd=lint_cmd, test_cmd="go test ./...", language="go")


def _detect_rust(root: Path) -> ProjectTooling | None:
    if not (root / "Cargo.toml").exists():
        return None
    return ProjectTooling(
        lint_cmd="cargo clippy -- -D warnings",
        test_cmd="cargo test",
        language="rust",
    )


def _any_exists(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


_DETECTORS = [_detect_python, _detect_javascript, _detect_go, _detect_rust]


def detect_project_tooling(cwd: str) -> ProjectTooling | None:
    """Scan project root for config files and return lint/test commands."""
    root = Path(cwd)
    for detector in _DETECTORS:
        result = detector(root)
        if result is not None:
            return result
    return None


def format_tooling_block(tooling: ProjectTooling) -> str:
    """Format detected tooling as a compact text block for prompt injection."""
    parts: list[str] = []
    if tooling.language:
        parts.append(f"Language: {tooling.language}")
    if tooling.lint_cmd:
        parts.append(f"Lint: `{tooling.lint_cmd}`")
    if tooling.test_cmd:
        parts.append(f"Test: `{tooling.test_cmd}`")
    return "\n".join(parts)
