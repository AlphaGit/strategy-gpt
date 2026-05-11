"""Top-level CLI. Subcommands surface the trusted Rust crates and the
in-house orchestrator. Commands whose underlying capability has not landed
yet are registered with explicit ``not implemented`` exits so the surface
remains discoverable as phases complete.

Surface (rewrite-architecture task 13.1):
- ``version`` — print the installed package version.
- ``fetch`` — pull a dataset through the data gateway.
- ``cache-stats`` — summarize the on-disk blob store.
- ``recent-decisions`` — dump the ledger's recent-decision view.
- ``replay`` — reconstruct a recorded run's BatchSpec + dataset.
- ``run`` — submit a BatchSpec to the engine (requires engine-worker binary).
- ``ingest`` — KB ingestion (phase 8).
- ``hypothesize`` — hypothesis-loop entry (phase 9).
- ``optimize`` — parameter optimizer entry (phase 10/11 wiring).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .engine import Engine
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


@app.command()
def replay(
    run_id: Annotated[str, typer.Option(help="Ledger run id to replay.")],
    ledger_root: Annotated[Path, typer.Option(help="Ledger root directory.")] = Path("ledger"),
    gateway_root: Annotated[Path, typer.Option(help="Gateway root directory (cache).")] = Path(
        "cache"
    ),
) -> None:
    """Reconstruct a recorded run's BatchSpec + dataset from the ledger.

    Realizes `experiment-ledger::reproducibility-from-ledger-alone`: the
    ledger plus the local cache are sufficient to reproduce the run
    byte-identically. Prints the full JSON envelope produced by
    ``Ledger.replay_run`` (``{batch_spec, bars, manifest_hash, warnings,
    run}``).
    """
    led = Ledger(ledger_root)
    gw = Gateway(gateway_root)
    typer.echo(led.replay_run(gw, run_id))


@app.command()
def run(  # noqa: PLR0913 — typer command surface; resource caps + paths.
    spec: Annotated[Path, typer.Option(help="Path to a JSON BatchSpec file.")],
    artifact: Annotated[Path, typer.Option(help="Compiled strategy cdylib path.")],
    worker: Annotated[Path, typer.Option(help="Path to the engine-worker binary.")],
    bars: Annotated[Path, typer.Option(help="Path to a JSON file with the bar list.")],
    dataset_manifest: Annotated[
        str, typer.Option(help="Dataset manifest hash (pass-through to the ledger).")
    ] = "",
    time_cap_secs: Annotated[float | None, typer.Option(help="Per-run wall-clock cap.")] = None,
    mem_cap_bytes: Annotated[int | None, typer.Option(help="Per-run memory cap (Linux).")] = None,
) -> None:
    """Submit a BatchSpec to the engine and print the result handle.

    The engine runs one subprocess per `RunSpec`; this command returns the
    opaque job handle so callers can poll separately. Use ``--time-cap-secs``
    and ``--mem-cap-bytes`` to enforce coordinator caps.
    """
    spec_payload = json.loads(spec.read_text())
    bars_payload = json.loads(bars.read_text())
    eng = Engine(worker, time_cap_secs=time_cap_secs, mem_cap_bytes=mem_cap_bytes)
    handle = eng.submit_batch(artifact, bars_payload, spec_payload, dataset_manifest)
    typer.echo(handle)


@app.command()
def ingest() -> None:
    """KB ingestion (phase 8 — not implemented yet)."""
    raise typer.Exit(
        code=_unimplemented("ingest", phase="8 (knowledge-base ingestion)"),
    )


@app.command()
def hypothesize() -> None:
    """Run the hypothesis loop (phase 9 — not implemented yet)."""
    raise typer.Exit(
        code=_unimplemented("hypothesize", phase="9 (hypothesis-loop)"),
    )


@app.command()
def optimize() -> None:
    """Run the parameter optimizer (phase 10/11 wiring — not implemented yet)."""
    raise typer.Exit(
        code=_unimplemented(
            "optimize",
            phase="10/11 (tester wiring + optimizer driver)",
        ),
    )


def _unimplemented(name: str, *, phase: str) -> int:
    typer.echo(
        f"`{name}` is not implemented yet; lands with phase {phase}.",
        err=True,
    )
    return 2


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
