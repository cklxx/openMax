"""Smoke tests for the openmax CLI."""

from click.testing import CliRunner

from openmax.cli import main


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "openmax" in result.output


def test_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "orchestration" in result.output.lower()


def test_run_help():
    result = CliRunner().invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--keep-panes" in result.output
