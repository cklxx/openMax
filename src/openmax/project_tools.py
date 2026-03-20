"""Auto-detect project lint, format, and test commands from config files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectTooling:
    """Detected lint and test commands for a single language."""

    lint_cmd: str | None = None
    test_cmd: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class MultiProjectTooling:
    """Detected tooling for all languages in a project."""

    toolings: tuple[ProjectTooling, ...] = ()
    primary: ProjectTooling | None = None

    @property
    def lint_cmd(self) -> str | None:
        return self.primary.lint_cmd if self.primary else None

    @property
    def test_cmd(self) -> str | None:
        return self.primary.test_cmd if self.primary else None

    @property
    def language(self) -> str | None:
        return self.primary.language if self.primary else None


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

_JS_LOCK_FILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
)


def _detect_js_package_manager(root: Path) -> str:
    for lockfile, manager in _JS_LOCK_FILES:
        if (root / lockfile).exists():
            return manager
    return "npm"


def _detect_python(root: Path) -> ProjectTooling | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() and not (root / "setup.py").exists():
        return None

    lint_cmd = None
    test_cmd = None

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

    if lint_cmd is None:
        for filename, cmd in _PYTHON_LINTERS.items():
            if (root / filename).exists():
                lint_cmd = cmd
                break

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

    pm = _detect_js_package_manager(root)
    run_prefix = f"{pm} run" if pm != "bun" else "bun run"
    lint_cmd = None
    test_cmd = None

    try:
        import json

        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})
        if "lint" in scripts:
            lint_cmd = f"{run_prefix} lint"
        if "test" in scripts:
            test_cmd = f"{run_prefix} test"
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    if lint_cmd is None:
        for filename, cmd in _JS_LINTERS.items():
            if (root / filename).exists():
                lint_cmd = cmd
                break

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


def _detect_makefile(root: Path) -> ProjectTooling | None:
    makefile = root / "Makefile"
    if not makefile.exists():
        return None
    try:
        text = makefile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lint_cmd = "make lint" if "\nlint:" in text or text.startswith("lint:") else None
    test_cmd = "make test" if "\ntest:" in text or text.startswith("test:") else None
    if lint_cmd is None and test_cmd is None:
        return None
    return ProjectTooling(lint_cmd=lint_cmd, test_cmd=test_cmd, language=None)


def _any_exists(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


_DETECTORS = [
    _detect_python,
    _detect_javascript,
    _detect_go,
    _detect_rust,
    _detect_makefile,
]

_LANG_PRIORITY = {"python": 0, "typescript": 1, "javascript": 1, "go": 2, "rust": 3}


def _pick_primary(toolings: list[ProjectTooling]) -> ProjectTooling:
    return min(toolings, key=lambda t: _LANG_PRIORITY.get(t.language or "", 99))


def detect_all_tooling(cwd: str) -> MultiProjectTooling:
    """Scan project root and return tooling for all detected languages."""
    root = Path(cwd)
    found: list[ProjectTooling] = []
    for detector in _DETECTORS:
        result = detector(root)
        if result is not None:
            found.append(result)
    if not found:
        return MultiProjectTooling()
    return MultiProjectTooling(
        toolings=tuple(found),
        primary=_pick_primary(found),
    )


def detect_project_tooling(cwd: str) -> ProjectTooling | None:
    """Scan project root for config files and return lint/test commands.

    Backward-compatible: returns only the primary detected language.
    """
    multi = detect_all_tooling(cwd)
    return multi.primary


def format_tooling_block(tooling: ProjectTooling | MultiProjectTooling) -> str:
    """Format detected tooling as a compact text block for prompt injection."""
    if isinstance(tooling, MultiProjectTooling):
        return _format_multi_tooling(tooling)
    return _format_single_tooling(tooling)


def _format_single_tooling(tooling: ProjectTooling) -> str:
    parts: list[str] = []
    if tooling.language:
        parts.append(f"Language: {tooling.language}")
    if tooling.lint_cmd:
        parts.append(f"Lint: `{tooling.lint_cmd}`")
    if tooling.test_cmd:
        parts.append(f"Test: `{tooling.test_cmd}`")
    return "\n".join(parts)


def _format_multi_tooling(multi: MultiProjectTooling) -> str:
    if not multi.toolings:
        return ""
    if len(multi.toolings) == 1:
        return _format_single_tooling(multi.toolings[0])
    sections: list[str] = []
    for t in multi.toolings:
        label = f"[{t.language}]" if t.language else "[unknown]"
        if t is multi.primary:
            label += " (primary)"
        parts = [label]
        if t.lint_cmd:
            parts.append(f"  Lint: `{t.lint_cmd}`")
        if t.test_cmd:
            parts.append(f"  Test: `{t.test_cmd}`")
        sections.append("\n".join(parts))
    return "\n".join(sections)
