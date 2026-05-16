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
- ``optimize`` — parameter optimizer (per-fold search + cross-fold OOS validation).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from . import __version__
from . import experiment_spec as espec
from .benchmark import format_report, report_json, run_benchmark
from .engine import Engine
from .gateway import Gateway
from .ledger import Ledger
from .optimization_ledger import (
    OptimizationLedger,
    build_replay_batch,
    find_trial,
    opt_dir_for,
    read_best,
    read_manifest,
    read_trials,
)
from .optimization_runner import run_optimization
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


optimize_app = typer.Typer(
    help="Parameter optimization — per-fold search, recursive grid, benchmark.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(optimize_app, name="optimize")


@optimize_app.callback(invoke_without_command=True)
def optimize_root(  # noqa: PLR0913 — CLI options + composition.
    ctx: typer.Context,
    spec: Annotated[
        Path | None,
        typer.Option(help="Path to an experiment-spec YAML/JSON. Required for a run."),
    ] = None,
    objective: Annotated[
        Path | None,
        typer.Option(help="Objective spec YAML/JSON. Defaults to objective.yaml next to --spec."),
    ] = None,
    worker: Annotated[
        Path,
        typer.Option(help="Path to the engine-worker binary."),
    ] = Path("crates/target/debug/engine-worker"),
    gateway_root: Annotated[
        Path,
        typer.Option(help="Gateway cache root used for bars resolution."),
    ] = Path("cache"),
    ledger_root: Annotated[
        Path,
        typer.Option(help="Optimization ledger root."),
    ] = Path("ledger"),
    method: Annotated[
        str | None,
        typer.Option(help="Override experiment.optimize.method."),
    ] = None,
    benchmark: Annotated[
        bool,
        typer.Option("--benchmark", help="Run benchmark + cost prediction before launching."),
    ] = False,
    sample: Annotated[
        int,
        typer.Option(help="Benchmark sample size."),
    ] = 3,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip post-benchmark confirmation prompt."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of plain text."),
    ] = False,
) -> None:
    """Run a per-fold optimization or invoke an ``optimize`` subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    if spec is None:
        typer.echo(
            "`strategy-gpt optimize` requires --spec, or a subcommand (`inspect`, `replay`).",
            err=True,
        )
        raise typer.Exit(code=2)

    experiment = espec.load(spec)
    if method is not None:
        if experiment.optimize is None:
            typer.echo("--method requires an `optimize` block in the spec.", err=True)
            raise typer.Exit(code=2)
        experiment = experiment.model_copy(
            update={"optimize": experiment.optimize.model_copy(update={"method": method})}
        )
    if experiment.optimize is None or experiment.folds is None:
        typer.echo(
            "experiment-spec is missing `optimize` and/or `folds` blocks.",
            err=True,
        )
        raise typer.Exit(code=2)

    bars_list, dataset_manifest = _resolve_bars(experiment, gateway_root)
    obj_path = objective if objective is not None else spec.parent / "objective.yaml"
    obj = _load_objective(obj_path)

    eng = Engine(
        worker,
        time_cap_secs=experiment.caps.time_cap_secs,
        mem_cap_bytes=experiment.caps.mem_cap_bytes,
    )

    opt_id = _opt_id(experiment)

    benchmark_report = None
    if benchmark:
        benchmark_report = run_benchmark(
            experiment=experiment,
            engine=eng,
            artifact_path=experiment.artifact,
            bars=bars_list,
            dataset_manifest=dataset_manifest,
            sample_size=sample,
        )
        typer.echo(report_json(benchmark_report) if json_out else format_report(benchmark_report))
        if not yes and not typer.confirm("Proceed with the full optimization?", default=True):
            raise typer.Exit(code=0)

    writer = OptimizationLedger(ledger_root)
    if benchmark_report is not None:
        # write_benchmark requires start() to have set opt_dir; do that lazily.
        pass
    result = run_optimization(
        experiment=experiment,
        objective=obj,
        engine=eng,
        artifact_path=experiment.artifact,
        bars=bars_list,
        dataset_manifest=dataset_manifest,
        opt_id=opt_id,
        persist_writer=writer,
    )
    if benchmark_report is not None:
        writer.write_benchmark(benchmark_report)
    typer.echo(_format_result(result, ledger_root, opt_id, as_json=json_out))


@optimize_app.command("inspect")
def optimize_inspect(
    opt_id: Annotated[str, typer.Argument(help="Optimization id to inspect.")],
    trial: Annotated[
        int | None,
        typer.Option(help="If set, dump a single trial row instead of the summary."),
    ] = None,
    ledger_root: Annotated[
        Path,
        typer.Option(help="Optimization ledger root."),
    ] = Path("ledger"),
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON."),
    ] = False,
) -> None:
    """Show an optimization run's manifest, best, or a specific trial."""
    opt_dir = opt_dir_for(ledger_root, opt_id)
    if not opt_dir.exists():
        typer.echo(f"no optimization at {opt_dir}", err=True)
        raise typer.Exit(code=1)
    if trial is not None:
        record = find_trial(opt_dir, trial)
        if record is None:
            typer.echo(f"trial {trial} not found in {opt_dir}", err=True)
            raise typer.Exit(code=1)
        payload = {
            "trial_id": record.trial_id,
            "round": record.round,
            "phase": record.phase,
            "fold_index": record.fold_index,
            "params": record.params,
            "seed": record.seed,
            "metrics": record.metrics,
            "score": record.score,
            "accepted": record.accepted,
            "reject_reason": record.reject_reason,
            "wall_secs": record.wall_secs,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    manifest = read_manifest(opt_dir)
    best = read_best(opt_dir)
    if json_out:
        typer.echo(
            json.dumps(
                {"manifest": manifest, "best": best, "trial_count": len(read_trials(opt_dir))},
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"opt_id: {opt_id}")
    typer.echo(f"status: {manifest.get('status')}")
    typer.echo(f"method: {manifest.get('method')}")
    typer.echo(f"folds: {len(manifest.get('folds', []))}")
    typer.echo(f"parallelism: {manifest.get('resolved_parallelism')}")
    typer.echo(f"trial_count: {manifest.get('trial_count')}")
    if best is not None and best.get("final") is not None:
        final = best["final"]
        typer.echo(f"best score: {final['aggregate_score']:.6f}")
        typer.echo(f"best params: {json.dumps(final['params'], sort_keys=True)}")
    else:
        typer.echo("best: (no candidate passed the objective constraints)")


@optimize_app.command("replay")
def optimize_replay(  # noqa: PLR0913 — CLI options surface is wide.
    opt_id: Annotated[str, typer.Argument(help="Optimization id.")],
    trial: Annotated[int, typer.Option(help="Trial id to replay.")],
    worker: Annotated[
        Path,
        typer.Option(help="Path to the engine-worker binary."),
    ] = Path("crates/target/debug/engine-worker"),
    gateway_root: Annotated[
        Path,
        typer.Option(help="Gateway cache root."),
    ] = Path("cache"),
    ledger_root: Annotated[
        Path,
        typer.Option(help="Optimization ledger root."),
    ] = Path("ledger"),
    out: Annotated[
        Path | None,
        typer.Option(help="Write the BacktestResult JSON to this path."),
    ] = None,
) -> None:
    """Replay a single recorded trial; reconstructs the BatchSpec from manifest + parquet."""
    opt_dir = opt_dir_for(ledger_root, opt_id)
    if not opt_dir.exists():
        typer.echo(f"no optimization at {opt_dir}", err=True)
        raise typer.Exit(code=1)
    record = find_trial(opt_dir, trial)
    if record is None:
        typer.echo(f"trial {trial} not found in {opt_dir}", err=True)
        raise typer.Exit(code=1)
    manifest = read_manifest(opt_dir)
    batch_spec = build_replay_batch(manifest, record)
    es = manifest["experiment_spec"]
    bars_block = es.get("bars", {})
    if "dataset" in bars_block:
        bars_list = _load_cached_bars(gateway_root, bars_block["dataset"])
    else:
        request = BarRequest.model_validate(bars_block["request"])
        gw = Gateway(gateway_root)
        if request.provider == "yfinance":
            gw.register_yfinance_provider(request.provider)
        response = gw.fetch(request, "prefer_cache")
        bars_list = list(response.bars)
    eng = Engine(worker)
    handle = eng.submit_batch(
        Path(es["artifact"]),
        bars_list,
        batch_spec,
        manifest["dataset_manifest"],
    )
    while True:
        status = eng.poll(handle)
        if status.status in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)
    if status.status != "completed":
        typer.echo(
            f"replay failed: status={status.status} error={status.error}",
            err=True,
        )
        raise typer.Exit(code=1)
    results = status.results or []
    if not results:
        typer.echo("replay produced no result entries.", err=True)
        raise typer.Exit(code=1)
    payload = json.dumps(results[0], indent=2, sort_keys=True)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload)
        typer.echo(str(out))
    else:
        typer.echo(payload)


def _load_objective(path: Path) -> dict[str, Any]:
    if not path.exists():
        msg = (
            f"objective spec not found at {path}; pass --objective explicitly "
            "or place an `objective.yaml` next to the experiment-spec."
        )
        raise typer.BadParameter(msg)
    raw = path.read_text()
    payload = yaml.safe_load(raw) if path.suffix.lower() in (".yaml", ".yml") else json.loads(raw)
    if not isinstance(payload, dict):
        msg = f"objective spec at {path} must be a mapping."
        raise typer.BadParameter(msg)
    return payload


def _opt_id(experiment: espec.ExperimentSpec) -> str:
    canonical = json.dumps(
        json.loads(experiment.model_dump_json()),
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.blake2b(canonical, digest_size=8).hexdigest()


def _format_result(result: Any, ledger_root: Path, opt_id: str, *, as_json: bool) -> str:  # noqa: ANN401
    if as_json:
        return json.dumps(
            {
                "opt_id": result.opt_id,
                "trial_count": len(result.trial_rows),
                "rejected_count": sum(1 for r in result.trial_rows if not r.accepted),
                "fold_winners": [
                    {
                        "fold_index": fw.fold_index,
                        "params": fw.params,
                        "train_score": fw.train_score,
                    }
                    for fw in result.fold_winners
                ],
                "final": (
                    {
                        "params": result.final.params,
                        "score": result.final.aggregate_score,
                        "aggregate_metrics": result.final.aggregate_metrics,
                    }
                    if result.final is not None
                    else None
                ),
                "ledger": str(opt_dir_for(ledger_root, opt_id)),
            },
            indent=2,
            sort_keys=True,
        )
    rejected = sum(1 for r in result.trial_rows if not r.accepted)
    lines = [
        f"opt_id: {result.opt_id}",
        f"folds: {len(result.folds)}",
        f"parallelism: {result.resolved_parallelism}",
        f"trials: {len(result.trial_rows)} (rejected {rejected})",
    ]
    if result.final is not None:
        lines.append("best:")
        lines.append(f"  params: {json.dumps(result.final.params, sort_keys=True)}")
        agg = json.dumps(result.final.aggregate_metrics, sort_keys=True)
        lines.append(f"  aggregate_metrics: {agg}")
        lines.append(f"  score: {result.final.aggregate_score:.6f}")
        lines.append(
            "  fold winners: ["
            + ", ".join(json.dumps(fw.params, sort_keys=True) for fw in result.fold_winners)
            + "]"
        )
    else:
        lines.append("best: (no candidate passed the objective constraints)")
    lines.append(f"ledger: {opt_dir_for(ledger_root, opt_id)}")
    return "\n".join(lines)


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
