"""Tests for project tooling auto-detection."""

from __future__ import annotations

import json

from openmax.project_tools import ProjectTooling, detect_project_tooling, format_tooling_block


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
