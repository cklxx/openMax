from __future__ import annotations

import textwrap

import pytest

from openmax.archetypes import (
    BUILT_IN_ARCHETYPES,
    format_archetype_context,
    format_subtask_hints,
    get_all_archetypes,
    load_custom_archetypes,
)
from openmax.archetypes._base import (
    Archetype,
    SubtaskTemplate,
    classify_task,
    match_archetype,
)

# --- dataclass construction ---


class TestSubtaskTemplateDefaults:
    def test_defaults_applied(self):
        t = SubtaskTemplate(name="build", description="Build it", files_pattern="src/*.py")
        assert t.dependencies == []
        assert t.agent_type == "claude-code"
        assert t.estimated_minutes == 5


class TestArchetypeCreation:
    def test_all_fields(self):
        templates = [SubtaskTemplate(name="s1", description="d", files_pattern="*")]
        arch = Archetype(
            name="test",
            display_name="Test Arch",
            description="A test archetype",
            indicators=["setup.py", "pyproject.toml"],
            subtask_templates=templates,
            planning_hints=["hint1"],
            anti_patterns=["anti1"],
        )
        assert arch.name == "test"
        assert arch.display_name == "Test Arch"
        assert len(arch.subtask_templates) == 1
        assert arch.planning_hints == ["hint1"]


# --- classify_task ---


class TestClassifyTask:
    def test_web_keywords_score_highest(self):
        scores = classify_task("build a React frontend page")
        assert scores["web"] > scores["cli"]
        assert scores["web"] > 0

    def test_cli_keywords(self):
        scores = classify_task("add a new CLI subcommand with argparse")
        assert scores["cli"] > scores["web"]

    def test_api_keywords(self):
        scores = classify_task("add a REST endpoint with FastAPI")
        assert scores["api"] > scores["cli"]

    def test_library_keywords(self):
        scores = classify_task("publish a Python SDK package to PyPI")
        assert scores["library"] > scores["web"]

    def test_refactor_keywords(self):
        scores = classify_task("refactor the auth module and migrate to new schema")
        assert scores["refactor"] > scores["web"]

    def test_empty_task_returns_zeros(self):
        scores = classify_task("")
        assert all(v == 0.0 for v in scores.values())

    def test_all_categories_present(self):
        scores = classify_task("anything")
        assert set(scores.keys()) == {"web", "cli", "api", "library", "refactor"}


# --- match_archetype ---


def _make_archetype(name: str, indicators: list[str] | None = None) -> Archetype:
    return Archetype(
        name=name,
        display_name=name.title(),
        description=f"Test {name}",
        indicators=indicators or [],
    )


class TestMatchArchetype:
    def test_matches_by_task_keywords(self):
        archetypes = [_make_archetype("web"), _make_archetype("cli")]
        result = match_archetype("build a React frontend", archetypes)
        assert result is not None
        assert result.name == "web"

    def test_matches_by_file_indicators(self):
        arch = _make_archetype("web", indicators=["package.json"])
        result = match_archetype("do something", [arch], ["package.json", "src/"])
        assert result is not None
        assert result.name == "web"

    def test_returns_none_when_no_archetypes(self):
        assert match_archetype("anything", []) is None

    def test_returns_none_when_confidence_too_low(self):
        arch = _make_archetype("web", indicators=["very_specific_file.xyz"])
        result = match_archetype("unrelated task", [arch])
        assert result is None

    def test_combines_task_and_file_scores(self):
        web = _make_archetype("web", indicators=["index.html"])
        cli = _make_archetype("cli", indicators=["cli.py"])
        result = match_archetype(
            "build a web page",
            [web, cli],
            project_files=["index.html"],
        )
        assert result is not None
        assert result.name == "web"

    def test_none_project_files_handled(self):
        arch = _make_archetype("web", indicators=["package.json"])
        result = match_archetype("build React app", [arch], project_files=None)
        assert result is not None


# --- Built-in archetypes ---


class TestBuiltInArchetypes:
    def test_five_built_in(self):
        assert len(BUILT_IN_ARCHETYPES) == 5

    def test_all_have_required_fields(self):
        for arch in BUILT_IN_ARCHETYPES:
            assert arch.name
            assert arch.display_name
            assert arch.description
            assert len(arch.indicators) >= 3

    def test_all_have_subtask_templates(self):
        for arch in BUILT_IN_ARCHETYPES:
            assert len(arch.subtask_templates) >= 3

    def test_all_have_planning_hints(self):
        for arch in BUILT_IN_ARCHETYPES:
            assert len(arch.planning_hints) >= 3

    def test_all_have_anti_patterns(self):
        for arch in BUILT_IN_ARCHETYPES:
            assert len(arch.anti_patterns) >= 2


# --- load_custom_archetypes ---


class TestLoadCustomArchetypes:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert load_custom_archetypes(str(tmp_path)) == []

    def test_loads_valid_yaml(self, tmp_path):
        arch_dir = tmp_path / ".openmax" / "archetypes"
        arch_dir.mkdir(parents=True)
        yaml_content = textwrap.dedent("""\
            name: custom
            display_name: Custom Archetype
            description: A test archetype
            indicators:
              - custom.yaml
            subtask_templates:
              - name: step1
                description: First step
                files_pattern: "src/**/*.py"
            planning_hints:
              - Do the thing
            anti_patterns:
              - Don't do the bad thing
        """)
        (arch_dir / "custom.yaml").write_text(yaml_content)
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")
        result = load_custom_archetypes(str(tmp_path))
        assert len(result) == 1
        assert result[0].name == "custom"

    def test_invalid_yaml_skipped(self, tmp_path):
        arch_dir = tmp_path / ".openmax" / "archetypes"
        arch_dir.mkdir(parents=True)
        (arch_dir / "broken.yaml").write_text(":::invalid:::")
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")
        result = load_custom_archetypes(str(tmp_path))
        assert result == []


class TestGetAllArchetypes:
    def test_includes_builtins(self, tmp_path):
        result = get_all_archetypes(str(tmp_path))
        assert len(result) >= 5


# --- format functions ---


class TestFormatArchetypeContext:
    def test_contains_archetype_name(self):
        arch = BUILT_IN_ARCHETYPES[0]
        output = format_archetype_context(arch, "build something")
        assert arch.display_name in output
        assert "build something" in output

    def test_contains_planning_hints(self):
        arch = BUILT_IN_ARCHETYPES[0]
        output = format_archetype_context(arch, "task")
        assert "Planning hints" in output


class TestFormatSubtaskHints:
    def test_lists_all_templates(self):
        arch = BUILT_IN_ARCHETYPES[0]
        output = format_subtask_hints(arch)
        for t in arch.subtask_templates:
            assert t.name in output

    def test_empty_templates_returns_empty(self):
        arch = _make_archetype("empty")
        assert format_subtask_hints(arch) == ""

    def test_shows_dependencies(self):
        arch = Archetype(
            name="test",
            display_name="Test",
            description="test",
            indicators=[],
            subtask_templates=[
                SubtaskTemplate(
                    name="step2",
                    description="Second",
                    files_pattern="*",
                    dependencies=["step1"],
                )
            ],
        )
        output = format_subtask_hints(arch)
        assert "after: step1" in output
