"""Tests for the employee management module."""

from __future__ import annotations

from pathlib import Path

import pytest

from openmax.employees import (
    _MAX_EXPERIENCE_ENTRIES,
    Employee,
    append_experience,
    build_employee_context,
    create_employee,
    employees_dir,
    extract_learnings,
    get_employee,
    list_employees,
    remove_employee,
    save_employee,
)


@pytest.fixture(autouse=True)
def _patch_employees_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openmax.employees._EMPLOYEES_DIR", tmp_path / "employees")


class TestCRUD:
    def test_create_and_get(self) -> None:
        emp = create_employee("alice", role="reviewer", specialty="Python, testing")
        assert emp.name == "alice"
        assert emp.role == "reviewer"
        assert emp.specialty == "Python, testing"
        assert emp.path.exists()

        loaded = get_employee("alice")
        assert loaded is not None
        assert loaded.name == "alice"
        assert loaded.specialty == "Python, testing"

    def test_get_nonexistent(self) -> None:
        assert get_employee("nobody") is None

    def test_list_empty(self) -> None:
        assert list_employees() == []

    def test_list_multiple(self) -> None:
        create_employee("bob")
        create_employee("alice")
        names = [e.name for e in list_employees()]
        assert names == ["alice", "bob"]

    def test_remove(self) -> None:
        create_employee("charlie")
        assert remove_employee("charlie")
        assert get_employee("charlie") is None

    def test_remove_nonexistent(self) -> None:
        assert not remove_employee("nobody")

    def test_save_preserves_fields(self) -> None:
        emp = create_employee("dave", agent_type="codex", specialty="React")
        emp.task_count = 5
        save_employee(emp)
        loaded = get_employee("dave")
        assert loaded is not None
        assert loaded.task_count == 5
        assert loaded.agent_type == "codex"


class TestExperience:
    def test_append_experience(self) -> None:
        create_employee("eve")
        result = append_experience("eve", "fix-auth", "- JWT handling improved")
        assert result
        emp = get_employee("eve")
        assert emp is not None
        assert emp.task_count == 1
        assert len(emp.experience_entries) == 1
        assert "fix-auth" in emp.experience_entries[0]

    def test_append_nonexistent(self) -> None:
        assert not append_experience("nobody", "task", "learnings")

    def test_experience_fifo(self) -> None:
        create_employee("fifo")
        for i in range(_MAX_EXPERIENCE_ENTRIES + 5):
            append_experience("fifo", f"task-{i}", f"- lesson {i}")
        emp = get_employee("fifo")
        assert emp is not None
        assert len(emp.experience_entries) == _MAX_EXPERIENCE_ENTRIES
        assert "task-0" not in emp.experience_entries[0]

    def test_roundtrip_with_experience(self) -> None:
        create_employee("grace", specialty="Go")
        append_experience("grace", "api-refactor", "- Use interfaces for DI")
        append_experience("grace", "perf-fix", "- pprof showed alloc hotspot")
        emp = get_employee("grace")
        assert emp is not None
        assert len(emp.experience_entries) == 2
        assert emp.task_count == 2
        assert "api-refactor" in emp.experience_entries[0]
        assert "perf-fix" in emp.experience_entries[1]


class TestContext:
    def test_build_context_basic(self) -> None:
        emp = Employee(name="test", specialty="Python", body="## Identity\n\nA tester.")
        ctx = build_employee_context(emp)
        assert "Employee Profile: test" in ctx
        assert "Specialty: Python" in ctx
        assert "A tester." in ctx
        assert "Learnings Protocol" in ctx

    def test_build_context_with_experience(self) -> None:
        emp = Employee(
            name="exp",
            experience_entries=["### 2026-01-01 — task1\n- lesson 1"],
        )
        ctx = build_employee_context(emp)
        assert "## Experience" in ctx
        assert "lesson 1" in ctx

    def test_build_context_truncates_long_experience(self) -> None:
        entries = [f"### 2026-01-{i:02d} — task-{i}\n- {'x' * 200}" for i in range(20)]
        emp = Employee(name="long", experience_entries=entries)
        ctx = build_employee_context(emp, char_budget=500)
        assert "## Experience" in ctx


class TestExtractLearnings:
    def test_extract_from_report(self) -> None:
        report = (
            "## Status\ndone\n\n## Summary\nDid stuff\n\n"
            "## Learnings\n- Learned A\n- Learned B\n\n## Changes\nfile.py"
        )
        result = extract_learnings(report)
        assert result is not None
        assert "Learned A" in result
        assert "Learned B" in result

    def test_extract_no_learnings(self) -> None:
        report = "## Status\ndone\n\n## Summary\nDid stuff"
        assert extract_learnings(report) is None

    def test_extract_learnings_at_end(self) -> None:
        report = "## Status\ndone\n\n## Learnings\n- Final lesson"
        result = extract_learnings(report)
        assert result is not None
        assert "Final lesson" in result


class TestEmployeesDir:
    def test_employees_dir_returns_path(self, tmp_path: Path) -> None:
        assert employees_dir() == tmp_path / "employees"
