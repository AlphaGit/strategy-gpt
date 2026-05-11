"""Smoke tests for the CLI scaffold that don't require the native module."""

from __future__ import annotations

from typer.testing import CliRunner

from strategy_gpt.cli import app

runner = CliRunner()


def test_version_prints() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    # version is a non-empty string of digits + dots.
    assert result.stdout.strip()


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("version", "fetch", "cache-stats", "recent-decisions"):
        assert cmd in result.stdout


def test_fetch_help_documents_options() -> None:
    result = runner.invoke(app, ["fetch", "--help"])
    assert result.exit_code == 0
    for opt in ("--provider", "--symbol", "--start", "--end"):
        assert opt in result.stdout
