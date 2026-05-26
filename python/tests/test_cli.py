"""Smoke tests for the CLI scaffold that don't require the native module."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from strategy_gpt.cli import app

runner = CliRunner()

# Pin a wide terminal so rich's Options panel doesn't truncate option names
# (e.g. `--run-id` → `…`) when CliRunner runs under a narrow tty (CI).
_WIDE_ENV = {"COLUMNS": "200"}

# Rich injects ANSI styling inside option names ("\x1b[1;36m--\x1b[0m\x1b[1;36mrun-id\x1b[0m")
# when FORCE_COLOR is set in the environment (the case on GitHub Actions), which breaks naive
# substring assertions. Strip CSI sequences before searching.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(s: str) -> str:
    return _ANSI_CSI_RE.sub("", s)


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
    result = runner.invoke(app, ["replay", "--help"], env=_WIDE_ENV)
    assert result.exit_code == 0
    plain = _plain(result.stdout)
    for opt in ("--run-id", "--ledger-root", "--gateway-root"):
        assert opt in plain


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
    result = runner.invoke(app, ["fetch", "--help"], env=_WIDE_ENV)
    assert result.exit_code == 0
    plain = _plain(result.stdout)
    for opt in ("--provider", "--symbol", "--start", "--end"):
        assert opt in plain
