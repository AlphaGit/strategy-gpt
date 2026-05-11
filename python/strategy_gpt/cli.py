"""Top-level CLI. Subcommands land incrementally; this scaffolding owns
the typer app + a small set of operations against the trusted Rust crates.

Currently:
- ``version`` — print the installed package version.
- ``fetch`` — exercise the data gateway end-to-end.
- ``cache-stats`` — summarize the on-disk blob store.
- ``recent-decisions`` — dump the ledger's recent-decision view as JSON.

Heavier subcommands (``ingest``, ``run``, ``hypothesize``, ``optimize``,
``replay``) land with their respective phases.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .gateway import Gateway
from .ledger import Ledger
from .types import AdjustmentPolicy, BarRequest, CacheMode, Resolution

app = typer.Typer(help="Strategy-GPT research loop CLI.")


@app.callback()
def _root() -> None:
    """No-op root callback so subcommands can be added incrementally."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def fetch(  # noqa: PLR0913 — typer commands naturally accept many CLI options.
    provider: Annotated[str, typer.Option(help="Provider name registered on the gateway.")],
    symbol: Annotated[str, typer.Option(help="Instrument symbol.")],
    start: Annotated[datetime, typer.Option(help="Start timestamp (UTC, ISO 8601).")],
    end: Annotated[datetime, typer.Option(help="End timestamp (UTC, ISO 8601).")],
    root: Annotated[Path, typer.Option(help="Gateway root directory.")] = Path("cache"),
    csv_provider_dir: Annotated[
        Path | None,
        typer.Option(help="If set, register a CSV provider at this path under --provider."),
    ] = None,
    resolution: Annotated[Resolution, typer.Option(help="Bar resolution.")] = Resolution.DAY,
    adjustment: Annotated[
        AdjustmentPolicy, typer.Option(help="Price adjustment policy.")
    ] = AdjustmentPolicy.BACK_ADJUSTED,
    mode: Annotated[
        str,
        typer.Option(help="Cache mode: prefer_cache | validate | force_refresh | offline."),
    ] = "prefer_cache",
) -> None:
    """Fetch a dataset and print a JSON summary to stdout."""
    gw = Gateway(root)
    if csv_provider_dir is not None:
        gw.register_csv_provider(provider, csv_provider_dir)
    request = BarRequest(
        provider=provider,
        symbol=symbol,
        start=start,
        end=end,
        resolution=resolution,
        adjustment=adjustment,
    )
    cache_mode = _validate_cache_mode(mode)
    response = gw.fetch(request, cache_mode)
    summary = {
        "bar_count": len(response.bars),
        "manifest_hash": response.manifest_hash,
        "manifest_blobs": response.manifest,
        "warning_count": len(response.warnings),
    }
    typer.echo(json.dumps(summary, indent=2))


@app.command("cache-stats")
def cache_stats(
    root: Annotated[Path, typer.Option(help="Gateway root directory.")] = Path("cache"),
) -> None:
    """Print blob count and total bytes for a gateway root."""
    stats = Gateway(root).cache_stats()
    typer.echo(json.dumps({"blob_count": stats.blob_count, "total_bytes": stats.total_bytes}))


@app.command("recent-decisions")
def recent_decisions(
    root: Annotated[Path, typer.Option(help="Ledger root directory.")] = Path("ledger"),
    limit: Annotated[int, typer.Option(help="Max rows to return.")] = 25,
) -> None:
    """Print the recent-decision view from the ledger as JSON."""
    raw = Ledger(root).recent_decisions(limit)
    typer.echo(raw)


_CACHE_MODES: frozenset[str] = frozenset(("prefer_cache", "validate", "force_refresh", "offline"))


def _validate_cache_mode(mode: str) -> CacheMode:
    """Validate cache mode against the allowed Literal values."""
    if mode not in _CACHE_MODES:
        msg = f"unknown cache mode `{mode}`; expected one of {sorted(_CACHE_MODES)}"
        raise typer.BadParameter(msg)
    # mode is guaranteed to be one of the Literal values at this point.
    return mode  # type: ignore[return-value]


if __name__ == "__main__":
    app()
