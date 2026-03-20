"""Tests for project tooling auto-detection."""

from __future__ import annotations

import json

from openmax.project_tools import (
    MultiProjectTooling,
    ProjectTooling,
    detect_all_tooling,
    detect_project_tooling,
    format_tooling_block,
)

# ---------------------------------------------------------------------------
# Single-language detection (backward compat via detect_project_tooling)
# ---------------------------------------------------------------------------


def test_detect_python_ruff_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "tests").mkdir()
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "python"
    assert "ruff check" in result.lint_cmd
    assert "ruff format --check" in result.lint_cmd
    assert result.test_cmd == "pytest"


def test_detect_python_pytest_from_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "pytest"


def test_detect_python_flake8(tmp_path):
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()")
    (tmp_path / ".flake8").write_text("[flake8]\nmax-line-length = 100\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "flake8 ."


def test_detect_python_mypy_appended(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n"
    )
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert "ruff check" in result.lint_cmd
    assert "mypy ." in result.lint_cmd


def test_detect_javascript_eslint(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "javascript"
    assert result.lint_cmd == "npm run lint"


def test_detect_javascript_with_test(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint .", "test": "jest"}})
    )
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "npm run lint"
    assert result.test_cmd == "npm run test"


def test_detect_typescript(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    (tmp_path / "tsconfig.json").write_text("{}")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "typescript"


def test_detect_javascript_biome(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({}))
    (tmp_path / "biome.json").write_text("{}")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert "biome check" in result.lint_cmd


def test_detect_javascript_prettier_appended(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    (tmp_path / ".prettierrc").write_text("{}")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert "npm run lint" in result.lint_cmd
    assert "prettier --check" in result.lint_cmd


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/m\ngo 1.21\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "go"
    assert result.lint_cmd == "go vet ./..."
    assert result.test_cmd == "go test ./..."


def test_detect_go_golangci_lint(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/m\ngo 1.21\n")
    (tmp_path / ".golangci.yml").write_text("linters:\n  enable:\n    - gofmt\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "golangci-lint run"


def test_detect_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'example'\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "rust"
    assert "cargo clippy" in result.lint_cmd
    assert result.test_cmd == "cargo test"


def test_detect_python_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\nrequests\n")
    (tmp_path / "tests").mkdir()
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.language == "python"
    assert result.test_cmd == "pytest"


def test_detect_no_project(tmp_path):
    result = detect_project_tooling(str(tmp_path))
    assert result is None


def test_format_tooling_block():
    tooling = ProjectTooling(lint_cmd="ruff check .", test_cmd="pytest", language="python")
    block = format_tooling_block(tooling)
    assert "Language: python" in block
    assert "Lint: `ruff check .`" in block
    assert "Test: `pytest`" in block


def test_format_tooling_block_partial():
    tooling = ProjectTooling(lint_cmd="go vet ./...", language="go")
    block = format_tooling_block(tooling)
    assert "Lint:" in block
    assert "Test:" not in block


# ---------------------------------------------------------------------------
# Package manager detection for JS/TS
# ---------------------------------------------------------------------------


def test_detect_pnpm(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "pnpm run test"


def test_detect_yarn(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    (tmp_path / "yarn.lock").write_text("")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "yarn run lint"


def test_detect_bun(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "bun:test"}}))
    (tmp_path / "bun.lockb").write_text("")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "bun run test"


def test_detect_bun_text_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "bun:test"}}))
    (tmp_path / "bun.lock").write_text("")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "bun run test"


def test_detect_npm_default_no_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "npm run test"


def test_detect_npm_explicit_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.test_cmd == "npm run test"


# ---------------------------------------------------------------------------
# Multi-language detection (detect_all_tooling)
# ---------------------------------------------------------------------------


def test_detect_all_python_and_js(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint .", "test": "jest"}})
    )
    multi = detect_all_tooling(str(tmp_path))
    assert len(multi.toolings) == 2
    langs = {t.language for t in multi.toolings}
    assert "python" in langs
    assert "javascript" in langs
    assert multi.primary is not None
    assert multi.primary.language == "python"


def test_detect_all_go_and_rust(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/m\ngo 1.21\n")
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'example'\n")
    multi = detect_all_tooling(str(tmp_path))
    assert len(multi.toolings) == 2
    assert multi.primary.language == "go"


def test_detect_all_empty(tmp_path):
    multi = detect_all_tooling(str(tmp_path))
    assert len(multi.toolings) == 0
    assert multi.primary is None
    assert multi.lint_cmd is None
    assert multi.test_cmd is None
    assert multi.language is None


def test_detect_all_single_language(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'example'\n")
    multi = detect_all_tooling(str(tmp_path))
    assert len(multi.toolings) == 1
    assert multi.primary.language == "rust"
    assert multi.lint_cmd == "cargo clippy -- -D warnings"
    assert multi.test_cmd == "cargo test"


def test_detect_all_three_languages(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "go.mod").write_text("module example.com/m\ngo 1.21\n")
    multi = detect_all_tooling(str(tmp_path))
    assert len(multi.toolings) == 3
    assert multi.primary.language == "python"


# ---------------------------------------------------------------------------
# MultiProjectTooling backward-compat properties
# ---------------------------------------------------------------------------


def test_multi_tooling_properties_delegate_to_primary():
    primary = ProjectTooling(lint_cmd="ruff check .", test_cmd="pytest", language="python")
    secondary = ProjectTooling(
        lint_cmd="npm run lint", test_cmd="npm run test", language="javascript"
    )
    multi = MultiProjectTooling(toolings=(primary, secondary), primary=primary)
    assert multi.lint_cmd == "ruff check ."
    assert multi.test_cmd == "pytest"
    assert multi.language == "python"


# ---------------------------------------------------------------------------
# Makefile detection
# ---------------------------------------------------------------------------


def test_detect_makefile_lint_and_test(tmp_path):
    (tmp_path / "Makefile").write_text("lint:\n\tgolangci-lint run\n\ntest:\n\tgo test ./...\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "make lint"
    assert result.test_cmd == "make test"


def test_detect_makefile_lint_only(tmp_path):
    (tmp_path / "Makefile").write_text("lint:\n\truff check .\n\nbuild:\n\tpython -m build\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is not None
    assert result.lint_cmd == "make lint"
    assert result.test_cmd is None


def test_detect_makefile_no_targets(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tpython -m build\n")
    result = detect_project_tooling(str(tmp_path))
    assert result is None


def test_language_specific_preferred_over_makefile(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "Makefile").write_text("lint:\n\tmake ruff\n\ntest:\n\tmake pytest\n")
    multi = detect_all_tooling(str(tmp_path))
    assert multi.primary.language == "python"
    assert "ruff check" in multi.primary.lint_cmd


# ---------------------------------------------------------------------------
# format_tooling_block with MultiProjectTooling
# ---------------------------------------------------------------------------


def test_format_multi_tooling_single(tmp_path):
    multi = MultiProjectTooling(
        toolings=(ProjectTooling(lint_cmd="ruff check .", test_cmd="pytest", language="python"),),
        primary=ProjectTooling(lint_cmd="ruff check .", test_cmd="pytest", language="python"),
    )
    block = format_tooling_block(multi)
    assert "Language: python" in block
    assert "[python]" not in block


def test_format_multi_tooling_multiple():
    py = ProjectTooling(lint_cmd="ruff check .", test_cmd="pytest", language="python")
    js = ProjectTooling(lint_cmd="npm run lint", test_cmd="npm run test", language="javascript")
    multi = MultiProjectTooling(toolings=(py, js), primary=py)
    block = format_tooling_block(multi)
    assert "[python] (primary)" in block
    assert "[javascript]" in block
    assert "ruff check" in block
    assert "npm run lint" in block


def test_format_multi_tooling_empty():
    multi = MultiProjectTooling()
    block = format_tooling_block(multi)
    assert block == ""
