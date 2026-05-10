"""Top-level CLI placeholder. Subcommands land in task 13.1."""

from __future__ import annotations

import typer

from . import __version__

app = typer.Typer(help="Strategy-GPT research loop CLI (scaffolding).")


@app.callback()
def _root() -> None:
    """No-op root callback so subcommands can be added incrementally."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
