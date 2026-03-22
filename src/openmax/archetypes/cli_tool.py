from __future__ import annotations

from openmax.archetypes._base import Archetype, SubtaskTemplate

ARCHETYPE = Archetype(
    name="cli",
    display_name="CLI Tool",
    description="Command-line tool with argument parsing and terminal output",
    indicators=[
        "cli.py",
        "__main__.py",
        "argparse",
        "click",
        "typer",
        "console_scripts",
        "entry_points",
    ],
    subtask_templates=[
        SubtaskTemplate(
            name="argument_parsing",
            description="CLI argument/flag definitions and validation",
            files_pattern="src/**/cli.py",
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="core_logic",
            description="Main business logic invoked by CLI commands",
            files_pattern="src/**/*.py",
            estimated_minutes=10,
        ),
        SubtaskTemplate(
            name="output_formatting",
            description="Terminal output, colors, progress bars, tables",
            files_pattern="src/**/formatting.py",
            dependencies=["core_logic"],
            estimated_minutes=5,
        ),
        SubtaskTemplate(
            name="tests",
            description="CLI invocation tests with CliRunner or subprocess",
            files_pattern="tests/test_cli*.py",
            dependencies=["argument_parsing", "core_logic"],
            estimated_minutes=8,
        ),
    ],
    planning_hints=[
        "Check existing CLI framework (click/typer/argparse) before adding commands",
        "Ensure new subcommands follow existing naming conventions",
        "Validate that help text and error messages are user-friendly",
        "Consider piping and non-interactive mode for automation use cases",
    ],
    anti_patterns=[
        "Putting business logic directly in click/typer command functions",
        "Hard-coding paths instead of using CLI arguments or config",
        "Ignoring exit codes for scriptability",
    ],
)
