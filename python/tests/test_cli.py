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
    for cmd in (
        "version",
        "fetch",
        "cache-stats",
        "recent-decisions",
        "replay",
        "run",
        "ingest",
        "hypothesize",
        "optimize",
    ):
        assert cmd in result.stdout


def test_replay_help_documents_options() -> None:
    result = runner.invoke(app, ["replay", "--help"])
    assert result.exit_code == 0
    for opt in ("--run-id", "--ledger-root", "--gateway-root"):
        assert opt in result.stdout


def test_ingest_exits_unimplemented() -> None:
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 2
    assert "not implemented" in result.stderr


def test_hypothesize_exits_unimplemented() -> None:
    result = runner.invoke(app, ["hypothesize"])
    assert result.exit_code == 2


def test_optimize_requires_spec_or_subcommand() -> None:
    result = runner.invoke(app, ["optimize"])
    assert result.exit_code == 2


def test_fetch_help_documents_options() -> None:
    result = runner.invoke(app, ["fetch", "--help"])
    assert result.exit_code == 0
    for opt in ("--provider", "--symbol", "--start", "--end"):
        assert opt in result.stdout
