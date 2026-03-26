"""Employee management — persistent sub-agent identities with accumulated experience."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_EMPLOYEES_DIR = Path.home() / ".config" / "openmax" / "employees"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_EXPERIENCE_HEADER = "## Experience"
_LEARNINGS_RE = re.compile(r"##\s*Learnings\s*\n(.*?)(?=\n##|\Z)", re.DOTALL)
_MAX_EXPERIENCE_ENTRIES = 20


@dataclass
class Employee:
    name: str
    role: str = "writer"
    agent_type: str = ""
    specialty: str = ""
    created: str = ""
    task_count: int = 0
    body: str = ""
    experience_entries: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return _EMPLOYEES_DIR / f"{self.name}.md"


def employees_dir() -> Path:
    return _EMPLOYEES_DIR


def list_employees() -> list[Employee]:
    if not _EMPLOYEES_DIR.exists():
        return []
    return sorted(
        (_load(p) for p in _EMPLOYEES_DIR.glob("*.md")),
        key=lambda e: e.name,
    )


def get_employee(name: str) -> Employee | None:
    path = _EMPLOYEES_DIR / f"{name}.md"
    return _load(path) if path.exists() else None


def save_employee(emp: Employee) -> Path:
    _EMPLOYEES_DIR.mkdir(parents=True, exist_ok=True)
    emp.path.write_text(_serialize(emp), encoding="utf-8")
    return emp.path


def remove_employee(name: str) -> bool:
    path = _EMPLOYEES_DIR / f"{name}.md"
    if path.exists():
        path.unlink()
        return True
    return False


def create_employee(
    name: str,
    *,
    role: str = "writer",
    agent_type: str = "",
    specialty: str = "",
    identity: str = "",
) -> Employee:
    emp = Employee(
        name=name,
        role=role,
        agent_type=agent_type,
        specialty=specialty,
        created=date.today().isoformat(),
        body=_default_body(name, role, specialty, identity),
    )
    save_employee(emp)
    return emp


def append_experience(name: str, task_name: str, learnings: str) -> bool:
    """Append a learnings entry to an employee's experience section."""
    emp = get_employee(name)
    if emp is None:
        return False
    entry = f"### {date.today().isoformat()} — {task_name}\n{learnings.strip()}"
    emp.experience_entries.append(entry)
    if len(emp.experience_entries) > _MAX_EXPERIENCE_ENTRIES:
        emp.experience_entries = emp.experience_entries[-_MAX_EXPERIENCE_ENTRIES:]
    emp.task_count += 1
    save_employee(emp)
    return True


def build_employee_context(emp: Employee, char_budget: int = 3000) -> str:
    """Build the context block to inject into a sub-agent prompt."""
    sections = [f"## Employee Profile: {emp.name}"]
    if emp.specialty:
        sections.append(f"Specialty: {emp.specialty}")
    body = emp.body.strip()
    if body:
        sections.append(body)
    if emp.experience_entries:
        exp_text = "\n\n".join(emp.experience_entries)
        if len(exp_text) > char_budget:
            exp_text = exp_text[-char_budget:]
            exp_text = exp_text[exp_text.find("\n") + 1 :]
        sections.append(f"{_EXPERIENCE_HEADER}\n\n{exp_text}")
    sections.append(_learnings_instruction(emp.name))
    return "\n\n".join(sections)


def extract_learnings(report_text: str) -> str | None:
    """Extract ## Learnings section from a task report."""
    match = _LEARNINGS_RE.search(report_text)
    return match.group(1).strip() if match else None


def _learnings_instruction(name: str) -> str:
    return (
        "## Learnings Protocol\n\n"
        f"You are employee **{name}**. When your task is complete, include a "
        "`## Learnings` section in your report with key takeaways from this task "
        "(techniques that worked, pitfalls discovered, patterns worth reusing). "
        "Keep it concise — 2-5 bullet points."
    )


def _default_body(name: str, role: str, specialty: str, identity: str) -> str:
    if identity:
        return identity
    parts = [f"## Identity\n\nYou are **{name}**"]
    if specialty:
        parts[0] += f", a specialist in {specialty}"
    parts[0] += "."
    if role != "writer":
        parts[0] += f" Your primary role is {role}."
    return parts[0]


def _load(path: Path) -> Employee:
    text = path.read_text(encoding="utf-8")
    meta, body_text = _parse_frontmatter(text)
    body_part, entries = _split_experience(body_text)
    return Employee(
        name=meta.get("name", path.stem),
        role=meta.get("role", "writer"),
        agent_type=meta.get("agent_type", ""),
        specialty=meta.get("specialty", ""),
        created=meta.get("created", ""),
        task_count=int(meta.get("task_count", 0)),
        body=body_part.strip(),
        experience_entries=entries,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    body = text[match.end() :]
    return meta, body


def _split_experience(body: str) -> tuple[str, list[str]]:
    idx = body.find(_EXPERIENCE_HEADER)
    if idx == -1:
        return body, []
    before = body[:idx]
    after = body[idx + len(_EXPERIENCE_HEADER) :].strip()
    entries = re.split(r"\n(?=### )", after)
    entries = [e.strip() for e in entries if e.strip()]
    return before, entries


def _serialize(emp: Employee) -> str:
    lines = ["---"]
    lines.append(f"name: {emp.name}")
    lines.append(f"role: {emp.role}")
    if emp.agent_type:
        lines.append(f"agent_type: {emp.agent_type}")
    if emp.specialty:
        lines.append(f'specialty: "{emp.specialty}"')
    if emp.created:
        lines.append(f"created: {emp.created}")
    lines.append(f"task_count: {emp.task_count}")
    lines.append("---\n")
    if emp.body:
        lines.append(emp.body.strip())
    if emp.experience_entries:
        lines.append(f"\n{_EXPERIENCE_HEADER}\n")
        lines.append("\n\n".join(emp.experience_entries))
    return "\n".join(lines) + "\n"
