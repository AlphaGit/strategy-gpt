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
- ``run`` — submit an experiment-spec to the engine (requires engine-worker binary).
- ``ingest`` — KB ingestion (phase 8).
- ``hypothesize`` — hypothesis-loop entry (phase 9).
- ``optimize`` — parameter optimizer entry (phase 10/11 wiring).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter

from . import __version__
from . import experiment_spec as espec
from .engine import Engine
from .gateway import Gateway
from .ledger import Ledger
from .types import AdjustmentPolicy, Bar, BarRequest, CacheMode, Resolution

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
    elif provider == "yfinance":
        gw.register_yfinance_provider(provider)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
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
def run(
    spec: Annotated[
        Path,
        typer.Option(help="Path to an experiment-spec YAML or JSON file."),
    ],
    worker: Annotated[
        Path,
        typer.Option(help="Path to the engine-worker binary."),
    ] = Path("crates/target/debug/engine-worker"),
    gateway_root: Annotated[
        Path,
        typer.Option(help="Gateway cache root used for bars resolution."),
    ] = Path("cache"),
    wait: Annotated[
        bool,
        typer.Option(help="Block until the job finishes; print JobStatus JSON instead of handle."),
    ] = False,
    poll_interval_secs: Annotated[
        float, typer.Option(help="Poll interval when --wait is set.")
    ] = 0.5,
) -> None:
    """Submit an experiment-spec to the engine.

    The experiment-spec carries the artifact, bars source, engine
    config, run list, parallelism, and per-run resource caps. See
    `docs/experiment-spec.md` for the schema.

    Default behavior prints the opaque job handle so callers can poll
    separately. Pass ``--wait`` to block until the job reaches a terminal
    state and print the full ``JobStatus`` JSON (status + results /
    error).
    """
    parsed = espec.load(spec)
    bars_list, dataset_manifest = _resolve_bars(parsed, gateway_root)
    batch_spec = parsed.to_batch_spec(dataset_manifest)
    eng = Engine(
        worker,
        time_cap_secs=parsed.caps.time_cap_secs,
        mem_cap_bytes=parsed.caps.mem_cap_bytes,
    )
    handle = eng.submit_batch(parsed.artifact, bars_list, batch_spec, dataset_manifest)
    if not wait:
        typer.echo(handle)
        return
    while True:
        status = eng.poll(handle)
        if status.status in ("completed", "failed", "cancelled"):
            typer.echo(status.model_dump_json(indent=2))
            return
        time.sleep(poll_interval_secs)


def _resolve_bars(
    parsed: espec.ExperimentSpec,
    gateway_root: Path,
) -> tuple[list[Bar], str]:
    """Resolve an experiment-spec's `bars` block into a bar list and manifest hash."""
    if isinstance(parsed.bars, espec.RequestRef):
        gw = Gateway(gateway_root)
        request = parsed.bars.request
        if request.provider == "yfinance":
            gw.register_yfinance_provider(request.provider)
        response = gw.fetch(request, "prefer_cache")
        return list(response.bars), response.manifest_hash
    manifest = parsed.bars.dataset
    bars_list = _load_cached_bars(gateway_root, manifest)
    return bars_list, manifest


def _load_cached_bars(gateway_root: Path, manifest_hash: str) -> list[Bar]:
    """Load bars for an already-cached dataset by manifest hash.

    The current cache stores per-year parquet blobs; materializing them
    back into a Bar list requires the gateway's normalizer. Pending a
    direct ``Gateway.load_by_manifest(...)`` surface, the v1 loader
    requires the caller to have materialized the bars previously and
    raises a structured error otherwise.
    """
    materialized = gateway_root / "materialized" / f"{manifest_hash}.json"
    if not materialized.exists():
        msg = (
            f"dataset not cached: manifest hash {manifest_hash} has no materialized bars "
            f"at {materialized}. Either fetch the dataset via `strategy-gpt fetch` and "
            "materialize bars (see docs/cli-cookbook.md `Materialize cached bars to JSON`), "
            "or rewrite the spec to use `bars: { request: ... }` for auto-fetch."
        )
        raise typer.BadParameter(msg)
    return TypeAdapter(list[Bar]).validate_json(materialized.read_text())


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
