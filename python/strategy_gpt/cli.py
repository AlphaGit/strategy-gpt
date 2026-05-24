"""Top-level CLI. Subcommands surface the trusted Rust crates and the
in-house orchestrator. Commands whose driver isn't wired yet are
registered with explicit ``not implemented`` exits so the surface is
discoverable.

Surface:
- ``version`` — print the installed package version.
- ``fetch`` — pull a dataset through the data gateway.
- ``cache-stats`` — summarize the on-disk blob store.
- ``recent-decisions`` — dump the ledger's recent-decision view.
- ``replay`` — reconstruct a recorded run's BatchSpec + dataset.
- ``run`` — submit an experiment-spec to the engine (requires engine-worker binary).
- ``ingest`` — KB ingestion (stub; drive via Python).
- ``hypothesize`` — hypothesis-loop entry (stub; drive via Python).
- ``optimize`` — parameter optimizer (per-fold search + cross-fold OOS validation).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import typer

if TYPE_CHECKING:
    from .per_strategy_ledger import (
        DecisionRecordV2,
        HypothesisRecordV2,
        PerStrategyLedger,
    )
import yaml
from pydantic import TypeAdapter

from . import __version__
from . import experiment_spec as espec
from .author import (
    AuthorBudgetExhaustedError,
    AuthorDeps,
    AuthorReasoningClient,
    DialogError,
    RepairMenuChoice,
    SmokeRunner,
    SmokeRunResult,
    SmokeSpec,
    run_intent_dialog,
)
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
    reselect,
)
from .optimization_runner import SelectionOverrides, run_optimization
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

    The cache stores per-year parquet blobs; materializing them back into
    a Bar list requires the gateway's normalizer. Pending a direct
    ``Gateway.load_by_manifest(...)`` surface, this loader requires the
    caller to have materialized the bars previously and raises a
    structured error otherwise.
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
    """KB ingestion — CLI driver not implemented yet (drive via Python)."""
    raise typer.Exit(code=_unimplemented("ingest"))


@app.command()
def hypothesize(  # noqa: PLR0913 — CLI surface mirrors the workflow knobs
    strategy: Annotated[
        str, typer.Argument(help="Strategy crate name (e.g. vxx_volatility_range).")
    ],
    ledger_root: Annotated[
        Path,
        typer.Option(help="Root directory of the per-strategy ledger."),
    ] = Path("ledger"),
    baseline_from: Annotated[
        str | None,
        typer.Option(help="Optimization run id to load baseline-best from."),
    ] = None,
    baseline_defaults: Annotated[
        bool,
        typer.Option(help="Use baseline-defaults (no optimize) for the baseline."),
    ] = False,
    max_backtests: Annotated[
        int | None,
        typer.Option(help="Hard ceiling on total backtests across the run."),
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(help="Quick mode: small mini-optimize budget (16 trials)."),
    ] = False,
    borderline_k: Annotated[
        float,
        typer.Option(help="Mechanical-gate `k` (variance-aware floor coefficient)."),
    ] = 1.0,
    k_candidates: Annotated[
        int,
        typer.Option(help="Target number of accepted candidates per run."),
    ] = 3,
    iteration_budget: Annotated[
        int,
        typer.Option(help="Inner-loop iteration cap."),
    ] = 4,
    objective: Annotated[
        str | None,
        typer.Option("--objective", help="Objective metric (default: sharpe)."),
    ] = None,
    llm_critic: Annotated[
        bool,
        typer.Option(
            "--llm-critic",
            help="Use the LLM-backed verdict critic in place of the deterministic one.",
        ),
    ] = False,
    engine_worker: Annotated[
        Path,
        typer.Option("--engine-worker", help="Path to the engine-worker binary."),
    ] = Path("crates/target/debug/engine-worker"),
    cache_root: Annotated[
        Path,
        typer.Option("--cache-root", help="Build pipeline cache root."),
    ] = Path("cache/builds"),
    work_root: Annotated[
        Path,
        typer.Option("--work-root", help="Build pipeline scratch directory."),
    ] = Path("cache/build-work"),
    gateway_root: Annotated[
        Path,
        typer.Option("--gateway-root", help="Gateway cache root used for bars."),
    ] = Path("cache"),
    crates_dir: Annotated[
        Path,
        typer.Option(help="Workspace crates directory."),
    ] = Path("crates"),
    kb_store: Annotated[
        Path | None,
        typer.Option("--kb-store", help="KB store path (default: kb/store/)."),
    ] = None,
    rebuild_kb: Annotated[
        bool,
        typer.Option("--rebuild-kb", help="Force-rebuild the KB store from sources.toml."),
    ] = False,
    model_stage1: Annotated[
        str | None,
        typer.Option("--model-stage1", help="Override the stage-1 reasoning model."),
    ] = None,
    model_stage2: Annotated[
        str | None,
        typer.Option("--model-stage2", help="Override the stage-2 reasoning model."),
    ] = None,
    model_stage3: Annotated[
        str | None,
        typer.Option("--model-stage3", help="Override the stage-3 reasoning model."),
    ] = None,
    model_critique: Annotated[
        str | None,
        typer.Option("--model-critique", help="Override the critique-stage model."),
    ] = None,
    model_rank: Annotated[
        str | None,
        typer.Option("--model-rank", help="Override the rank-stage model."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress per-node progress output on stderr."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(help="Validate inputs and exit without invoking the workflow."),
    ] = False,
) -> None:
    """Hypothesis loop entry — runs the multi-stage emission + evaluation flow.

    Builds :class:`HypothesizeDeps` from the named crate plus the
    operator's environment, then invokes
    :func:`strategy_gpt.hypothesize.hypothesize` and prints a JSON
    summary. Failure modes detected before the workflow surface as
    typer errors naming the missing artifact and the suggested next
    step (run ``author`` first, run ``optimize`` first, set an API
    key, …). Tests drive the orchestrator directly via
    :func:`strategy_gpt.hypothesize.hypothesize` with stubs.
    """
    if baseline_from is not None and baseline_defaults:
        msg = "--baseline-from and --baseline-defaults are mutually exclusive"
        raise typer.BadParameter(msg)
    if baseline_from is None and not baseline_defaults:
        typer.echo(
            "no baseline provided; pass --baseline-from <optimize-run-id> or --baseline-defaults",
            err=True,
        )
        raise typer.Exit(code=2)

    model_overrides: dict[str, str] = {}
    for stage_key, value in (
        ("stage1", model_stage1),
        ("stage2", model_stage2),
        ("stage3", model_stage3),
        ("critique", model_critique),
        ("rank", model_rank),
    ):
        if value is not None:
            model_overrides[stage_key] = value

    _run_hypothesize(
        strategy=strategy,
        ledger_root=ledger_root,
        baseline_from=baseline_from,
        baseline_defaults=baseline_defaults,
        max_backtests=max_backtests,
        quick=quick,
        borderline_k=borderline_k,
        k_candidates=k_candidates,
        iteration_budget=iteration_budget,
        objective=objective,
        llm_critic=llm_critic,
        engine_worker=engine_worker,
        cache_root=cache_root,
        work_root=work_root,
        gateway_root=gateway_root,
        crates_dir=crates_dir,
        kb_store=kb_store,
        rebuild_kb=rebuild_kb,
        model_overrides=model_overrides,
        quiet=quiet,
        dry_run=dry_run,
    )


def _run_hypothesize(  # noqa: PLR0913, PLR0915 — orchestrates the construction surface
    *,
    strategy: str,
    ledger_root: Path,
    baseline_from: str | None,
    baseline_defaults: bool,
    max_backtests: int | None,
    quick: bool,
    borderline_k: float,
    k_candidates: int,
    iteration_budget: int,
    objective: str | None,
    llm_critic: bool,
    engine_worker: Path,
    cache_root: Path,
    work_root: Path,
    gateway_root: Path,
    crates_dir: Path,
    kb_store: Path | None,
    rebuild_kb: bool,
    model_overrides: dict[str, str],
    quiet: bool,
    dry_run: bool,
) -> None:
    """Construct deps + invoke the workflow. Surfaces as JSON on stdout."""
    from .author import load_intent_toml  # noqa: PLC0415
    from .build_pipeline import BuildPipeline  # noqa: PLC0415
    from .hypothesize import (  # noqa: PLC0415
        HypothesizeDeps,
        hypothesize,
        hypothesize_result_to_json,
    )
    from .hypothesize_wiring import (  # noqa: PLC0415
        MissingApiKeyError,
        MissingArtifactError,
        MissingOptimizeRunError,
        build_evaluate_fold,
        build_kb_client,
        build_stage_client,
        compute_baseline_defaults,
        load_baseline_from_optimize,
        resolve_crate_paths,
        resolve_kept_bounds,
        resolve_objective_metric,
        verify_api_keys,
    )
    from .per_strategy_ledger import PerStrategyLedger  # noqa: PLC0415
    from .reasoning import HypothesisLoopConfig, select_reasoning_model  # noqa: PLC0415

    try:
        crate_paths = resolve_crate_paths(strategy, crates_dir)
    except MissingArtifactError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    try:
        verify_api_keys()
    except MissingApiKeyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    if not engine_worker.is_file():
        typer.echo(
            f"engine-worker binary not found at {engine_worker}; "
            "build it via 'cd crates && cargo build -p engine-worker'",
            err=True,
        )
        raise typer.Exit(code=2)

    intent = load_intent_toml(crate_paths.crate_dir)
    objective_metric = resolve_objective_metric(
        {"objective_metric": None, **intent.param_schema_sketch},
        objective,
    )

    kb_store_path = kb_store if kb_store is not None else Path("kb/store")
    kb_sources_path = Path("kb/sources.toml")

    if dry_run:
        summary = {
            "dry_run": True,
            "strategy": strategy,
            "ledger_root": str(ledger_root),
            "baseline_source": (
                f"optimize_run:{baseline_from}" if baseline_from else "baseline_defaults"
            ),
            "objective_metric": objective_metric,
            "fold_source": (
                "experiment.yaml"
                if crate_paths.experiment_yaml is not None
                else "smoke.toml (single fold)"
            ),
            "stage_models": {**model_overrides},
            "engine_worker": str(engine_worker),
            "kb_store": str(kb_store_path),
            "rebuild_kb": rebuild_kb,
            "max_backtests": max_backtests,
            "quick": quick,
            "iteration_budget": iteration_budget,
            "k_candidates": k_candidates,
            "borderline_k": borderline_k,
            "llm_critic": llm_critic,
        }
        typer.echo(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return

    build_pipeline = BuildPipeline(
        cache_root=cache_root.resolve(),
        work_root=work_root.resolve(),
        engine_rt_path=(crates_dir / "engine-rt").resolve(),
        whitelist_path=(crates_dir / "build-pipeline" / "whitelist.toml").resolve(),
    )

    try:
        kb_client = build_kb_client(
            kb_store_path,
            kb_sources_path,
            rebuild=rebuild_kb,
            banner=lambda msg: typer.echo(msg, err=True),
        )
    except MissingArtifactError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    typed_overrides = cast(
        "dict[Any, str] | None",
        model_overrides or None,
    )
    stage_client = build_stage_client(model_overrides=typed_overrides)

    try:
        evaluate_fold, dataset_manifest, fold_count = build_evaluate_fold(
            crate_paths,
            build_pipeline=build_pipeline,
            engine_worker_path=engine_worker,
            gateway_root=gateway_root,
            quick_fold_count=1 if quick else None,
        )
    except MissingArtifactError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    try:
        if baseline_from is not None:
            baseline = load_baseline_from_optimize(
                baseline_from,
                ledger_root,
                crate_paths=crate_paths,
                objective_metric=objective_metric,
            )
        else:
            baseline = compute_baseline_defaults(
                crate_paths,
                evaluate_fold,
                fold_count,
                objective_metric=objective_metric,
            )
    except (MissingArtifactError, MissingOptimizeRunError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    kept_bounds = resolve_kept_bounds(intent.param_schema_sketch)

    config = HypothesisLoopConfig.with_defaults(
        reasoning_model=select_reasoning_model(),
        target_candidates=k_candidates,
        iteration_budget=iteration_budget,
    )

    verdict_critic: Any = None
    if llm_critic:
        # An LLM-backed critic is a follow-up; fall back to the
        # deterministic critic and warn so the operator knows.
        typer.echo(
            "--llm-critic requested but no LLM critic is wired yet; "
            "using DeterministicVerdictCritic.",
            err=True,
        )

    deps = HypothesizeDeps(
        kb=cast("Any", kb_client),
        stage_client=stage_client,
        build_pipeline=build_pipeline,
        evaluate_fold=evaluate_fold,
        prompt_api=strategy,
        allowed_metrics=[
            "sharpe",
            "sortino",
            "profit_factor",
            "win_ratio",
            "max_drawdown",
            "annualized_return",
            "n_trades",
            "avg_trade_length_bars",
        ],
        baseline_result=baseline.result,
        baseline_files=baseline.files,
        baseline_params_schema=baseline.params_schema,
        baseline_per_fold_scores=list(baseline.per_fold_scores),
        baseline_metrics=baseline.metrics,
        baseline_aggregate_score=baseline.aggregate_score,
        objective_metric=objective_metric,
        dataset_manifest_hash=dataset_manifest,
        kept_bounds=kept_bounds,
        verdict_critic=verdict_critic,
        engine_rt_src_dir=(crates_dir / "engine-rt" / "src").resolve(),
    )

    # borderline_k is a forward-looking knob on the workflow; the
    # current HypothesisLoopConfig dataclass is frozen+slotted so the
    # value cannot be stamped onto it without a schema change. The flag
    # is preserved on the CLI for forward compatibility but does not
    # override the workflow's default at this layer.
    del borderline_k

    ledger = PerStrategyLedger(ledger_root, strategy)

    if not quiet:
        typer.echo(
            f"[hypothesize] strategy={strategy} baseline={baseline.source} "
            f"folds={fold_count} objective={objective_metric} "
            f"budget(iter={iteration_budget},backtests={max_backtests or 'unbounded'})",
            err=True,
        )
    progress = None if quiet else _hypothesize_progress_renderer(
        iteration_budget=iteration_budget
    )
    attempt_sink = None if quiet else _hypothesize_attempt_sink()

    result = hypothesize(
        strategy,
        ledger=ledger,
        deps=deps,
        config=config,
        persist=True,
        max_backtests=max_backtests,
        progress=progress,
        attempt_sink=attempt_sink,
    )

    if not quiet:
        typer.echo(
            f"[hypothesize] done: accepted={len(result.accepted)} "
            f"rejected={len(result.rejected)} "
            f"termination={result.termination_reason.value} "
            f"iterations={result.iterations} "
            f"backtests_consumed={result.backtests_consumed}",
            err=True,
        )

    payload = json.loads(hypothesize_result_to_json(result))
    payload["baseline_source"] = baseline.source
    typer.echo(json.dumps(payload, indent=2, default=str))


def _hypothesize_attempt_sink() -> Callable[[str], None]:
    """Return a stderr printer for per-attempt LLM + build heartbeats.

    Stage-3 in particular runs ``cargo build`` inside the validator, so
    a single attempt can take minutes. Without this sink the operator
    sees nothing between the stage-3 "starting" line and the final
    accept/reject — and on repair retries it looks like the loop has
    stalled. The sink prints each phase transition (request,
    response, compile-start, compile-done) with elapsed timing.
    """

    def render(msg: str) -> None:
        typer.echo(f"    > {msg}", err=True)

    return render


def _hypothesize_progress_renderer(  # noqa: PLR0915 — one branch per workflow node
    *,
    iteration_budget: int,
) -> Callable[[str, Mapping[str, Any], Mapping[str, Any]], None]:
    """Per-node progress renderer that prints to stderr.

    Output is operator-facing: each line names what just happened plus
    the most relevant facts (candidate name + rationale, falsification
    criterion, observed vs claimed delta, gate stats with sigma,
    verdict reasons). The renderer reads the workflow's typed deltas
    so the summary stays accurate when the workflow evolves.
    """
    counter = {"iter": 0}

    def _first_sentence(text: str, *, max_chars: int = 180) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return ""
        if "." in cleaned:
            head = cleaned.split(".", 1)[0].strip()
            if head:
                cleaned = head
        return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 1] + "…"

    def _echo(line: str, *, indent: int = 0) -> None:
        prefix = "  " * indent
        typer.echo(f"{prefix}{line}", err=True)

    def _diagnose(delta: Mapping[str, Any]) -> None:
        d = delta.get("diagnosis")
        if d is None:
            _echo("• diagnose: (no diagnosis emitted)")
            return
        metrics = getattr(d, "metrics", None)
        sharpe = getattr(metrics, "sharpe", None) if metrics is not None else None
        ar = getattr(metrics, "annualized_return", None) if metrics is not None else None
        dd = getattr(metrics, "max_drawdown", None) if metrics is not None else None
        n_trades = getattr(metrics, "n_trades", None) if metrics is not None else None
        head = (
            f"• diagnose: baseline sharpe={sharpe:.3f} return={ar:.2%} "
            f"max_dd={dd:.2%} trades={n_trades}"
            if metrics is not None
            else "• diagnose: baseline metrics unavailable"
        )
        _echo(head)
        regimes = getattr(d, "regime_performance", []) or []
        if regimes:
            worst = min(regimes, key=lambda r: getattr(r, "sharpe", 0.0))
            label = getattr(worst, "label", "?")
            sh = getattr(worst, "sharpe", 0.0)
            _echo(f"weakest regime: {label} (sharpe={sh:.3f})", indent=2)
        misfires = getattr(d, "signal_misfires", []) or []
        if misfires:
            names = [getattr(m, "signal", "?") for m in misfires[:3]]
            _echo("signal misfires: " + ", ".join(names), indent=2)

    def _kb(delta: Mapping[str, Any], label: str) -> None:
        cites = delta.get("kb_cites") or []
        if not cites:
            _echo(f"• {label}: no relevant citations found")
            return
        sources: list[str] = []
        for c in cites[:3]:
            src = getattr(c, "source", None) or (c.get("source") if isinstance(c, dict) else None)
            if src:
                sources.append(str(src))
        snippet = ", ".join(sources) if sources else "various sources"
        _echo(f"• {label}: {len(cites)} citation(s) ({snippet})")

    def _stage1(delta: Mapping[str, Any]) -> None:
        counter["iter"] += 1
        header = f"━━━ iteration {counter['iter']}/{iteration_budget} ━━━"
        _echo(header)
        reject = delta.get("candidate_reject_kind")
        if reject:
            _echo(f"✗ stage1 (idea) failed: {reject}", indent=1)
            return
        idea = delta.get("stage1_idea")
        if idea is None:
            _echo("✗ stage1 emitted no idea", indent=1)
            return
        name = getattr(idea, "candidate_name", "?")
        conf = getattr(idea, "expected_lift_confidence", 0.0)
        side = list(getattr(idea, "expected_side_effects", []) or [])
        rationale = _first_sentence(getattr(idea, "rationale", ""))
        _echo(f"✓ stage1 idea: {name} (confidence={conf:.0%})", indent=1)
        if rationale:
            _echo(f"why: {rationale}", indent=2)
        if side:
            _echo(f"expected side-effects: {', '.join(side[:3])}", indent=2)

    def _cheap_critique(delta: Mapping[str, Any]) -> None:
        reject = delta.get("candidate_reject_kind")
        if reject:
            rationale = _first_sentence(str(delta.get("candidate_reject_rationale", "")))
            tail = f" — {rationale}" if rationale else ""
            _echo(f"✗ cheap_critique rejected ({reject}){tail}", indent=1)
        else:
            _echo("✓ cheap_critique passed (idea worth committing)", indent=1)

    def _stage2(delta: Mapping[str, Any]) -> None:
        reject = delta.get("candidate_reject_kind")
        if reject:
            _echo(f"✗ stage2 (commitments) failed: {reject}", indent=1)
            return
        stage2 = delta.get("stage2_parsed")
        if stage2 is None:
            _echo("✓ stage2 ok", indent=1)
            return
        fal = getattr(stage2, "falsification", {}) or {}
        primary = fal.get("primary", {}) if isinstance(fal, dict) else {}
        metric = primary.get("metric", "?")
        direction = primary.get("direction", "?")
        delta_v = primary.get("delta_vs_baseline", 0.0)
        guard_count = len(fal.get("guard_constraints", [])) if isinstance(fal, dict) else 0
        _echo(
            f"✓ stage2 commitments: must beat baseline {metric} by {direction} {delta_v:+.4f} "
            f"({guard_count} guard(s))",
            indent=1,
        )
        pi = getattr(stage2, "param_intent", {}) or {}
        if isinstance(pi, dict):
            added = [a.get("name", "?") for a in pi.get("added", [])]
            kept = list(pi.get("kept", []))
            removed = list(pi.get("removed", []))
            parts = []
            if added:
                parts.append(f"adds {','.join(added)}")
            if kept:
                parts.append(f"keeps {','.join(kept)}")
            if removed:
                parts.append(f"removes {','.join(removed)}")
            if parts:
                _echo("params: " + "; ".join(parts), indent=2)

    stage3_names_preview = 4
    rank_names_preview = 3

    def _stage3(delta: Mapping[str, Any]) -> None:
        from .reject_taxonomy import is_mechanical  # noqa: PLC0415

        reject = delta.get("candidate_reject_kind")
        if reject:
            rationale = _first_sentence(str(delta.get("candidate_reject_rationale", "")))
            tail = f" — {rationale}" if rationale else ""
            label = (
                "deferred (mechanical: hypothesis preserved)"
                if is_mechanical(reject)
                else "failed"
            )
            _echo(f"✗ stage3 (code emission) {label}: {reject}{tail}", indent=1)
            return
        files = delta.get("stage3_parsed")
        file_map = getattr(files, "files", {}) or {} if files else {}
        names = sorted(file_map.keys())
        head = ", ".join(names[:stage3_names_preview]) + (
            "..." if len(names) > stage3_names_preview else ""
        )
        _echo(f"✓ stage3 built strategy crate ({len(names)} files: {head})", indent=1)

    def _mini_optimize(delta: Mapping[str, Any]) -> None:
        reject = delta.get("candidate_reject_kind")
        if reject:
            rationale = _first_sentence(str(delta.get("candidate_reject_rationale", "")))
            tail = f" — {rationale}" if rationale else ""
            _echo(f"✗ mini_optimize rejected ({reject}){tail}", indent=1)
            return
        attempt = delta.get("candidate_attempt_result")
        if attempt is None:
            _echo("✓ mini_optimize completed (no result detail)", indent=1)
            return
        agg = getattr(attempt, "aggregate_score", 0.0)
        base = getattr(attempt, "baseline_aggregate_score", 0.0)
        per_fold = list(getattr(attempt, "per_fold_best_scores", []) or [])
        consumed = delta.get("backtests_consumed")
        delta_pct = ((agg - base) / abs(base)) * 100 if base else float("inf")
        trend = "↑" if agg > base else ("↓" if agg < base else "≈")
        _echo(
            f"✓ mini_optimize {trend} candidate={agg:.4f} vs baseline={base:.4f} "
            f"({delta_pct:+.1f}% on objective; backtests_consumed={consumed})",
            indent=1,
        )
        if per_fold:
            fold_str = ", ".join(f"{s:.3f}" for s in per_fold)
            _echo(f"per-fold best: [{fold_str}]", indent=2)
        best = getattr(attempt, "best_params", {}) or {}
        if best:
            params_str = ", ".join(f"{k}={v}" for k, v in list(best.items())[:5])
            _echo(f"best params: {params_str}", indent=2)
        check = getattr(attempt, "falsification_check", None)
        if check is not None:
            classification = getattr(check, "classification", "?")
            observed = getattr(check, "primary_observed_delta", 0.0)
            target = getattr(check, "primary_delta_target", 0.0)
            _echo(
                f"falsification: {classification} (observed Δ={observed:+.4f}, "
                f"required {target:+.4f})",
                indent=2,
            )
        flags = list(getattr(attempt, "side_effect_flags", []) or [])
        if flags:
            _echo("side-effect flags: " + ", ".join(flags), indent=2)

    def _mechanical_gate(delta: Mapping[str, Any]) -> None:
        outcome = delta.get("gate_outcome")
        if outcome is None:
            return
        accept = getattr(outcome, "accept", False)
        rationale = _first_sentence(getattr(outcome, "rationale", "") or "")
        score_delta = getattr(outcome, "score_delta", 0.0)
        sigma = getattr(outcome, "sigma_combined", 0.0)
        k = getattr(outcome, "k", 1.0)
        floor = k * sigma
        fold_cv = getattr(outcome, "fold_cv", 0.0)
        cv_thresh = getattr(outcome, "fold_cv_threshold", 0.5)
        verdict = "✓ accept" if accept else "✗ reject"
        _echo(
            f"{verdict} mechanical_gate: delta={score_delta:+.4f} "
            f"vs floor {k}*sigma={floor:.4f} "
            f"(fold_cv={fold_cv:.3f}/{cv_thresh})",
            indent=1,
        )
        if rationale:
            _echo(f"why: {rationale}", indent=2)

    def _verdict(delta: Mapping[str, Any]) -> None:
        decision = delta.get("verdict_decision")
        if decision is None:
            return
        accept = getattr(decision, "accept", False)
        reasons = list(getattr(decision, "reasons", []) or [])
        rationale = _first_sentence(getattr(decision, "rationale", "") or "")
        verdict = "✓ accept" if accept else "✗ reject"
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        _echo(f"{verdict} verdict_critique{suffix}", indent=1)
        if rationale:
            _echo(f"why: {rationale}", indent=2)

    def _rank(delta: Mapping[str, Any]) -> None:
        from .reject_taxonomy import is_mechanical  # noqa: PLC0415

        accepted = list(delta.get("accepted") or [])
        rejected_all = list(delta.get("rejected") or [])
        deferred = [r for r in rejected_all if r.reject_kind and is_mechanical(r.reject_kind)]
        rejected = [r for r in rejected_all if r not in deferred]
        suffix_def = f", {len(deferred)} deferred" if deferred else ""
        if accepted:
            top = [
                getattr(a.candidate, "name", "?") for a in accepted[:rank_names_preview]
            ]
            suffix = "..." if len(accepted) > rank_names_preview else ""
            _echo(
                f"• rank: {len(accepted)} accepted "
                f"[{', '.join(top)}{suffix}], "
                f"{len(rejected)} rejected{suffix_def} so far",
                indent=1,
            )
        else:
            _echo(
                f"• rank: 0 accepted, {len(rejected)} rejected{suffix_def} so far",
                indent=1,
            )

    def _select(delta: Mapping[str, Any]) -> None:
        accepted = list(delta.get("accepted") or [])
        reason = delta.get("termination_reason")
        reason_val = getattr(reason, "value", reason)
        _echo("━━━ loop complete ━━━")
        if accepted:
            for a in accepted:
                name = getattr(a.candidate, "name", "?")
                metric = getattr(a.candidate, "target_metric", "?")
                rationale = _first_sentence(getattr(a, "rationale", "") or "")
                tail = f" — {rationale}" if rationale else ""
                _echo(f"✓ accepted: {name} (target={metric}){tail}", indent=1)
        else:
            _echo("no hypotheses accepted this run", indent=1)
        _echo(f"termination: {reason_val}", indent=1)

    dispatch: dict[str, Callable[[Mapping[str, Any]], None]] = {
        "diagnose": _diagnose,
        "kb_query": lambda d: _kb(d, "kb_query"),
        "kb_filter": lambda d: _kb(d, "kb_filter"),
        "generate_stage1_idea": _stage1,
        "cheap_critique": _cheap_critique,
        "generate_stage2_commitments": _stage2,
        "generate_stage3_files": _stage3,
        "mini_optimize": _mini_optimize,
        "mechanical_gate": _mechanical_gate,
        "verdict_critique": _verdict,
        "rank": _rank,
        "select": _select,
    }

    def render(
        node_name: str,
        delta: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> None:
        del state
        fn = dispatch.get(node_name)
        if fn is not None:
            fn(delta)

    return render


# ---------------------------------------------------------------------------
# `author` — interactive strategy creation
# ---------------------------------------------------------------------------


def _default_reasoning_client(model: str | None) -> AuthorReasoningClient:
    """Construct the default author-stage reasoning client adapter.

    Lazy import of the Anthropic SDK keeps the CLI cold-path light.
    Tests monkeypatch this factory to inject a stub.
    """
    from .reasoning import select_reasoning_model  # noqa: PLC0415

    model_obj = select_reasoning_model(override=None) if model is None else None
    if model is not None:
        # Operator-supplied override is treated as opaque; provider is
        # inferred from the prefix.
        from .reasoning import ReasoningModel  # noqa: PLC0415

        provider: Literal["anthropic", "openai"] = (
            "anthropic" if model.startswith("claude") else "openai"
        )
        model_obj = ReasoningModel(provider=provider, model_id=model)
    if model_obj is None:
        raise RuntimeError("could not resolve reasoning model")
    return (
        _AnthropicAuthorAdapter(model_obj.model_id)
        if model_obj.provider == "anthropic"
        else (_OpenAIAuthorAdapter(model_obj.model_id))
    )


class _AnthropicAuthorAdapter:
    """Anthropic Messages adapter exposing the author-stage surface."""

    def __init__(self, model_id: str) -> None:
        import anthropic  # noqa: PLC0415

        self._model_id = model_id
        self._client = anthropic.Anthropic()

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        response = self._client.messages.create(
            model=self._model_id,
            max_tokens=4096,
            system=system,
            messages=[{"role": m["role"], "content": m["content"]} for m in transcript],  # type: ignore[typeddict-item]
        )
        return _extract_anthropic_text(response.content, scope="dialog")

    def emit_files(self, *, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model_id,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return _extract_anthropic_text(response.content, scope="emit")


def _extract_anthropic_text(blocks: Any, *, scope: str) -> str:  # noqa: ANN401 — Anthropic SDK block union is opaque
    for block in blocks or []:
        if getattr(block, "type", None) == "text":
            return str(getattr(block, "text", ""))
    msg = f"Anthropic {scope} response had no text block"
    raise RuntimeError(msg)


class _OpenAIAuthorAdapter:
    """OpenAI Chat-Completions adapter exposing the author-stage surface."""

    def __init__(self, model_id: str) -> None:
        import openai  # noqa: PLC0415

        self._model_id = model_id
        self._client = openai.OpenAI()

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend({"role": m["role"], "content": m["content"]} for m in transcript)
        response = self._client.chat.completions.create(
            model=self._model_id,
            messages=messages,  # type: ignore[arg-type]
        )
        return str(response.choices[0].message.content or "")

    def emit_files(self, *, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return str(response.choices[0].message.content or "")


def _default_smoke_runner(
    *,
    engine_worker: Path,
    gateway_root: Path,
    seed: int = 0,
) -> SmokeRunner:
    """Build a smoke runner that fetches bars and submits a single-run batch.

    The runner mirrors :func:`strategy_gpt.tester.run_smoke`'s contract:
    success iff the run completes with at least one trade and no sanity
    trip. Closure captures the gateway + engine wiring so the resulting
    callable matches the :class:`SmokeRunner` signature.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from .tester import run_smoke  # noqa: PLC0415

    def runner(library_path: Path, spec: SmokeSpec) -> SmokeRunResult:
        gw = Gateway(gateway_root)
        if spec.provider == "yfinance":
            gw.register_yfinance_provider(spec.provider)
        request = BarRequest(
            provider=spec.provider,
            symbol=spec.symbol,
            start=datetime.fromisoformat(spec.start).replace(tzinfo=UTC),
            end=datetime.fromisoformat(spec.end).replace(tzinfo=UTC),
            resolution=Resolution(spec.resolution),
            adjustment=AdjustmentPolicy.BACK_ADJUSTED,
        )
        response = gw.fetch(request, "prefer_cache")
        engine = Engine(engine_worker)
        outcome = run_smoke(
            engine,
            strategy_artifact=str(library_path),
            dataset_ref=response.manifest_hash,
            bars=list(response.bars),
            params={},
            slice_start=request.start,
            slice_end=request.end,
            dataset_manifest=response.manifest_hash,
            seed=seed,
        )
        return SmokeRunResult(
            ok=outcome.ok,
            feedback=outcome.rationale,
            artifact_hash=str(library_path),
        )

    return runner


# Module-level hooks so tests can inject stubs without going through the
# real LLM / engine surfaces. Override by monkeypatching at the module.
_author_reasoning_client_factory: Any = _default_reasoning_client
_author_smoke_runner_factory: Any = _default_smoke_runner


_PASTE_BUFFER_PROBE_SECONDS = 0.05


def paste_aware_input(prompt: str) -> str:
    """``input()`` wrapper that concatenates pasted multi-line blocks.

    Terminals feed pasted text into stdin line-buffered: ``input(prompt)``
    consumes the first line and leaves the rest in the buffer. We probe
    stdin with a short ``select`` timeout after the initial read; any
    buffered lines arriving within the window are appended (joined by
    ``\\n``) and returned as a single multi-line string.

    This gives operators a "paste a block, press Enter once" UX without
    requiring them to type a sentinel like ``<<<`` first. The explicit
    sentinel mode in :func:`strategy_gpt.author.read_multiline_reply`
    remains available for *typing* multi-line input (where the operator
    can't rely on a paste burst to fire the probe).

    When stdin is not a TTY (CI, piped input, tests), the probe is
    skipped so behavior matches plain ``input()``.
    """
    import select  # noqa: PLC0415 — only used here
    import sys  # noqa: PLC0415 — local

    line = input(prompt)
    if not sys.stdin.isatty():
        return line
    extra: list[str] = []
    while select.select([sys.stdin], [], [], _PASTE_BUFFER_PROBE_SECONDS)[0]:
        nxt = sys.stdin.readline()
        if not nxt:
            break
        extra.append(nxt.rstrip("\n"))
    return "\n".join([line, *extra]) if extra else line


def _cli_repair_menu(exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
    """Interactive prompt for the four repair-exhaustion options.

    Reads from stdin via ``typer.prompt`` for short answers and via
    :func:`strategy_gpt.author.read_multiline_reply` for free-form
    guidance (operator can type ``<<<`` to enter multi-line mode).
    Tests inject a different ``repair_menu`` callable directly into
    ``run_author_session``.
    """
    from .author import read_multiline_reply  # noqa: PLC0415

    del exc
    typer.echo("")
    typer.echo("Repair budget exhausted. Choose how to proceed:")
    typer.echo("  1) Suggest an alternative approach in natural language")
    typer.echo("  2) Retry with an extended repair budget")
    typer.echo("  3) Edit a specific decision (mechanism, params, smoke, …)")
    typer.echo("  4) Abort")
    choice = typer.prompt("Choice [1-4]", default="4").strip()
    if choice == "1":
        typer.echo(
            "Describe the alternative approach (single line, paste a block, "
            "or `<<<` for multi-line typing):"
        )
        guidance = read_multiline_reply(ask_user=paste_aware_input, write_user=typer.echo)
        return RepairMenuChoice(kind="suggest_alternative", payload={"guidance": guidance})
    if choice == "2":
        new_k_emit = int(typer.prompt("New k_repair_emit", default="4"))
        new_k_build = int(typer.prompt("New k_repair_build", default="4"))
        return RepairMenuChoice(
            kind="extend_budget",
            payload={"k_repair_emit": new_k_emit, "k_repair_build": new_k_build},
        )
    if choice == "3":
        field_name = typer.prompt(
            "Field to revise [mechanism_summary | param_sketch | smoke_spec | universe]"
        )
        typer.echo(
            "How should it change? (single line, paste a block, or `<<<` for multi-line typing):"
        )
        guidance = read_multiline_reply(ask_user=paste_aware_input, write_user=typer.echo)
        return RepairMenuChoice(
            kind="edit_decision", payload={"field": field_name, "guidance": guidance}
        )
    return RepairMenuChoice(kind="abort", payload={})


def _cli_event_renderer(*, verbose: bool) -> Callable[[Any], None]:
    """Build an event-sink that prints human-readable progress lines.

    Default verbosity surfaces transition pairs as a single line (e.g.
    ``cargo build … done in 4.2s``). ``verbose=True`` adds the raw
    cargo argv before the result so operators can copy/paste it. The
    BuildPipeline itself does not stream per-line stdout today, so
    ``--verbose`` is a forward-looking switch that we wire in here and
    pass through to the underlying subprocess later.
    """
    from .author_events import (  # noqa: PLC0415
        AuthorEvent,
        CargoBuildCompleted,
        CargoBuildProgress,
        CargoBuildStarted,
        FileWritten,
        LintCompleted,
        LintStarted,
        RepairAttemptCompleted,
        RepairAttemptStarted,
        SmokeFetchStarted,
        SmokeRunCompleted,
        SmokeRunStarted,
    )

    build_target: dict[str, str] = {"name": ""}

    def render(event: AuthorEvent) -> None:  # noqa: PLR0912 — one branch per event type
        match event:
            case RepairAttemptStarted(attempt=a, budget=b):
                typer.echo(f"[attempt {a + 1}/{b + 1}] starting emit/build/smoke")
            case RepairAttemptCompleted(attempt=a, outcome=o):
                typer.echo(f"[attempt {a + 1}] result: {o}")
            case FileWritten(path=p):
                size = ""
                # FileWritten doesn't carry a size; show the path with a small icon.
                typer.echo(f"  ✎ wrote {p}{size}")
            case LintStarted():
                typer.echo("  lint …", nl=False)
            case LintCompleted(ok=ok):
                typer.echo(" ok" if ok else " rejected")
            case CargoBuildStarted(args=args):
                # Extract the -p target so the operator can see what's being built.
                target = args[args.index("-p") + 1] if "-p" in args else "strategy"
                build_target["name"] = target
                if verbose:
                    typer.echo(f"  cargo build -p {target} (argv={args!r})")
                else:
                    typer.echo(f"  cargo build -p {target} … (compiling)")
            case CargoBuildProgress(elapsed_seconds=elapsed):
                target = build_target.get("name") or "strategy"
                typer.echo(f"    … still compiling {target} ({elapsed:.0f}s)")
            case CargoBuildCompleted(returncode=rc, duration_seconds=d):
                status = "done" if rc == 0 else "failed"
                typer.echo(f"  cargo build {status} in {d:.2f}s")
            case SmokeFetchStarted(symbol=sym, start=start, end=end):
                typer.echo(f"  fetching {sym} bars {start}..{end} …")
            case SmokeRunStarted():
                typer.echo("  running smoke …", nl=False)
            case SmokeRunCompleted(ok=ok, trade_count=tc, sanity_trips=st):
                summary = f"trades={tc}, sanity_trips={st}"
                typer.echo(f" {'ok' if ok else 'failed'} ({summary})")
            case _:
                pass

    return render


@app.command()
def author(  # noqa: PLR0913 — typer surface
    idea: Annotated[
        str | None,
        typer.Argument(help="Optional natural-language seed for the dialog stage."),
    ] = None,
    crates_dir: Annotated[
        Path,
        typer.Option(help="Workspace crates directory."),
    ] = Path("crates"),
    cache_root: Annotated[
        Path,
        typer.Option(help="Build pipeline cache root."),
    ] = Path("cache/builds"),
    work_root: Annotated[
        Path,
        typer.Option(help="Build pipeline scratch directory."),
    ] = Path("cache/build-work"),
    engine_worker: Annotated[
        Path,
        typer.Option(help="Path to the engine-worker binary."),
    ] = Path("crates/target/debug/engine-worker"),
    gateway_root: Annotated[
        Path,
        typer.Option(help="Gateway cache root used for smoke bars."),
    ] = Path("cache"),
    verify: Annotated[
        str | None,
        typer.Option("--verify", help="Set to `batch` to run the full-batch verification."),
    ] = None,
    k_repair_emit: Annotated[
        int,
        typer.Option("--k-repair-emit", help="Repair budget for the emit/build/smoke stage."),
    ] = 2,
    k_repair_build: Annotated[
        int,
        typer.Option("--k-repair-build", help="Repair budget for the build sub-stage."),
    ] = 2,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the reasoning model identifier."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress the locked-in decisions panel between turns."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Stream per-line cargo / rustc output during build."),
    ] = False,
) -> None:
    """Author a new strategy crate via interactive LLM dialog.

    Drives :func:`strategy_gpt.author.run_intent_dialog` to elicit a
    structured intent, then :func:`strategy_gpt.author.author_strategy`
    to emit, build, and smoke-test the crate. On success the crate path
    and a next-step hint are printed.
    """
    from .build_pipeline import BuildPipeline  # noqa: PLC0415
    from .repair import RepairConfig  # noqa: PLC0415

    if verify is not None and verify != "batch":
        raise typer.BadParameter("--verify only accepts the value `batch`")

    reasoning_client = _author_reasoning_client_factory(model)
    smoke_runner = _author_smoke_runner_factory(
        engine_worker=engine_worker,
        gateway_root=gateway_root,
    )
    # Resolve to absolute paths: the build pipeline lays the strategy
    # crate out under work_root, and cargo resolves the `engine-rt` path
    # dep relative to *that* dir. A relative `engine_rt_path` would point
    # to a non-existent sibling of the sandbox.
    build_pipeline = BuildPipeline(
        cache_root=cache_root.resolve(),
        work_root=work_root.resolve(),
        engine_rt_path=(crates_dir / "engine-rt").resolve(),
        whitelist_path=(crates_dir / "build-pipeline" / "whitelist.toml").resolve(),
    )

    from .author_decisions import DecisionRecord  # noqa: PLC0415
    from .author_events import noop_sink  # noqa: PLC0415

    event_sink = noop_sink if quiet else _cli_event_renderer(verbose=verbose)
    opened_record: list[DecisionRecord] = []
    try:
        intent = run_intent_dialog(
            seed=idea,
            reasoning_client=reasoning_client,
            crates_dir=crates_dir,
            model_name=model or "default",
            on_record_ready=opened_record.append,
            quiet=quiet,
            ask_user=paste_aware_input,
        )
    except DialogError as e:
        typer.echo(f"dialog failed: {e}", err=True)
        raise typer.Exit(code=1) from None

    decision_record_path = opened_record[0].path if opened_record else None
    deps = AuthorDeps(
        reasoning_client=reasoning_client,
        build_pipeline=build_pipeline,
        smoke_runner=smoke_runner,
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=k_repair_emit),
        repair_config_build=RepairConfig(k_repair=k_repair_build),
        decision_record_path=decision_record_path,
        event_sink=event_sink,
    )

    if verify == "batch" and intent.experiment_spec is None:
        typer.echo(
            "--verify=batch requested but the dialog did not produce an experiment_spec; "
            "re-run the dialog and ask it to populate experiment_spec.",
            err=True,
        )
        raise typer.Exit(code=1)

    from .author import run_author_session  # noqa: PLC0415

    try:
        result = run_author_session(
            intent,
            deps=deps,
            reasoning_client=reasoning_client,
            repair_menu=_cli_repair_menu,
            write_user=typer.echo,
        )
    except AuthorBudgetExhaustedError as e:
        typer.echo(f"author run aborted: {e}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        json.dumps(
            {
                "name": result.name,
                "crate_path": str(result.crate_path),
                "artifact_hash": result.artifact_hash,
                "next_steps": [
                    f"strategy-gpt hypothesize {result.name}",
                    f"strategy-gpt run --spec {result.crate_path}/experiment.yaml"
                    if intent.experiment_spec is not None
                    else f"# inspect: bat {result.crate_path}/src/lib.rs",
                ],
            },
            indent=2,
        )
    )


hypothesis_app = typer.Typer(
    help="Hypothesis-loop replay/diff commands over the per-strategy ledger.",
    invoke_without_command=False,
    no_args_is_help=True,
)
app.add_typer(hypothesis_app, name="hypothesis")


def _find_decision_record(
    ledger_root: Path,
    strategy: str | None,
    decision_id: str,
) -> tuple[str, PerStrategyLedger, DecisionRecordV2, HypothesisRecordV2]:
    """Locate ``decision_id`` under ``ledger_root``.

    When ``strategy`` is given the lookup is scoped to that subfolder;
    otherwise every strategy under ``ledger/strategies/`` is scanned.
    Returns ``(strategy, ledger, decision, hypothesis)``.
    """
    from .per_strategy_ledger import (  # noqa: PLC0415 — heavy import, defer to CLI
        PerStrategyLedger,
    )

    candidates: list[str]
    if strategy is not None:
        candidates = [strategy]
    else:
        strategies_dir = ledger_root / "strategies"
        if not strategies_dir.is_dir():
            msg = f"no per-strategy ledger under {strategies_dir}"
            raise typer.BadParameter(msg)
        candidates = sorted(p.name for p in strategies_dir.iterdir() if p.is_dir())

    for name in candidates:
        ledger = PerStrategyLedger(ledger_root, name)
        decision: DecisionRecordV2 | None = next(
            (d for d in ledger.decisions_iter() if d.id == decision_id),
            None,
        )
        if decision is None:
            continue
        hypothesis: HypothesisRecordV2 | None = next(
            (h for h in ledger.hypotheses_iter() if h.id == decision.hypothesis_id),
            None,
        )
        if hypothesis is None:
            msg = (
                f"decision {decision_id} found in strategy {name!r} but its "
                f"hypothesis row {decision.hypothesis_id!r} is missing"
            )
            raise typer.BadParameter(msg)
        return name, ledger, decision, hypothesis

    scanned = ", ".join(candidates)
    msg = f"decision_id {decision_id!r} not found (scanned: {scanned})"
    raise typer.BadParameter(msg)


@hypothesis_app.command("replay")
def hypothesis_replay(
    decision_id: Annotated[str, typer.Argument(help="DecisionRecord id to replay.")],
    ledger_root: Annotated[Path, typer.Option(help="Per-strategy ledger root.")] = Path("ledger"),
    strategy: Annotated[
        str | None,
        typer.Option(help="Optional strategy name; auto-scans all strategies if omitted."),
    ] = None,
) -> None:
    """Reconstruct a recorded candidate's files from source blobs.

    Loads the candidate's stage-3 source bundle from
    ``ledger/strategies/<strategy>/sources/<files_set_hash>/`` and
    prints a summary that downstream replay tooling (build + mini-
    optimize) consumes. Full mini-optimize replay needs the
    operator-specific engine + KB collaborators (`HypothesizeDeps`);
    those are constructed in Python, not the CLI.
    """
    name, ledger, decision, hypothesis = _find_decision_record(ledger_root, strategy, decision_id)
    files_set_hash = decision.evidence.get("files_set_hash", "") or hypothesis.baseline_files_hash
    try:
        files = ledger.read_source_set(files_set_hash) if files_set_hash else {}
    except FileNotFoundError as e:
        raise typer.BadParameter(str(e)) from e
    summary = {
        "strategy": name,
        "decision_id": decision_id,
        "hypothesis_id": hypothesis.id,
        "candidate_name": hypothesis.candidate_name,
        "outcome": decision.outcome.kind,
        "stage": decision.outcome.stage,
        "files_set_hash": files_set_hash,
        "files_in_bundle": sorted(files.keys()),
        "n_files": len(files),
        "param_intent_added": [a.name for a in hypothesis.param_intent.added],
        "param_intent_kept": list(hypothesis.param_intent.kept),
        "param_intent_removed": list(hypothesis.param_intent.removed),
        "falsification_primary": hypothesis.falsification.primary.model_dump(),
    }
    typer.echo(json.dumps(summary, indent=2, default=str))


@hypothesis_app.command("diff")
def hypothesis_diff(
    decision_id: Annotated[str, typer.Argument(help="DecisionRecord id to diff.")],
    ledger_root: Annotated[Path, typer.Option(help="Per-strategy ledger root.")] = Path("ledger"),
    strategy: Annotated[
        str | None,
        typer.Option(help="Optional strategy name; auto-scans all strategies if omitted."),
    ] = None,
) -> None:
    """Render unified diff between a candidate's files and the baseline source bundle.

    The candidate's source set hash is recorded on the decision evidence
    (``files_set_hash``); the baseline bundle hash is on the hypothesis
    record (``baseline_files_hash``). Both bundles are reconstructed
    from the per-strategy source store; ``difflib.unified_diff`` is
    rendered per file. Files present in one bundle and not the other
    are reported as full add/delete diffs.
    """
    import difflib  # noqa: PLC0415 — only the diff command needs it

    name, ledger, decision, hypothesis = _find_decision_record(ledger_root, strategy, decision_id)
    candidate_hash = decision.evidence.get("files_set_hash", "")
    baseline_hash = hypothesis.baseline_files_hash
    try:
        candidate_files = ledger.read_source_set(candidate_hash) if candidate_hash else {}
        baseline_files = ledger.read_source_set(baseline_hash) if baseline_hash else {}
    except FileNotFoundError as e:
        raise typer.BadParameter(str(e)) from e

    paths = sorted(set(candidate_files) | set(baseline_files))
    typer.echo(
        f"# strategy={name} decision_id={decision_id} "
        f"candidate={candidate_hash[:12] or 'EMPTY'} "
        f"baseline={baseline_hash[:12] or 'EMPTY'}"
    )
    for path in paths:
        base_text = baseline_files.get(path, "")
        cand_text = candidate_files.get(path, "")
        diff = difflib.unified_diff(
            base_text.splitlines(keepends=True),
            cand_text.splitlines(keepends=True),
            fromfile=f"baseline/{path}",
            tofile=f"candidate/{path}",
        )
        text = "".join(diff)
        if text:
            typer.echo(text, nl=False)


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
    robust_objective: Annotated[
        bool,
        typer.Option(
            "--robust-objective",
            help="Final-rank by parameter-sensitivity (robust) score in place of DSR.",
        ),
    ] = False,
    pbo_threshold: Annotated[
        float | None,
        typer.Option(
            "--pbo-threshold",
            min=0.0,
            max=1.0,
            help="Override the PBO rejection threshold (default 0.5).",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Publish a best despite a rejected_pbo decision; the override is recorded.",
        ),
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
    overrides = SelectionOverrides(
        force=force,
        pbo_threshold=pbo_threshold,
        robust_objective=robust_objective if robust_objective else None,
    )
    result = run_optimization(
        experiment=experiment,
        objective=obj,
        engine=eng,
        artifact_path=experiment.artifact,
        bars=bars_list,
        dataset_manifest=dataset_manifest,
        opt_id=opt_id,
        persist_writer=writer,
        selection_overrides=overrides,
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


@optimize_app.command("reselect")
def optimize_reselect(  # noqa: PLR0913 — flags mirror the selection layer.
    opt_id: Annotated[str, typer.Argument(help="Optimization id to reselect over.")],
    ledger_root: Annotated[Path, typer.Option(help="Optimization ledger root.")] = Path("ledger"),
    robust_objective: Annotated[
        bool, typer.Option("--robust-objective", help="Rank by robust score.")
    ] = False,
    no_robust_objective: Annotated[
        bool,
        typer.Option(
            "--no-robust-objective",
            help="Force DSR ranking (overrides the spec's robust_objective flag).",
        ),
    ] = False,
    pbo_threshold: Annotated[
        float | None,
        typer.Option("--pbo-threshold", min=0.0, max=1.0),
    ] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    top_k: Annotated[int | None, typer.Option("--top-k", min=2)] = None,
) -> None:
    """Re-run the selection layer over an existing optimization run."""
    opt_dir = opt_dir_for(ledger_root, opt_id)
    if not opt_dir.exists():
        typer.echo(f"no optimization at {opt_dir}", err=True)
        raise typer.Exit(code=1)
    robust: bool | None
    if robust_objective and no_robust_objective:
        typer.echo("pass at most one of --robust-objective / --no-robust-objective", err=True)
        raise typer.Exit(code=2)
    if robust_objective:
        robust = True
    elif no_robust_objective:
        robust = False
    else:
        robust = None
    out_path = reselect(
        opt_dir,
        robust_objective=robust,
        pbo_threshold=pbo_threshold,
        force=force,
        top_k=top_k,
    )
    typer.echo(str(out_path))


@optimize_app.command("compare")
def optimize_compare(
    opt_id: Annotated[str, typer.Argument(help="Optimization id.")],
    best_a: Annotated[str, typer.Argument(help="First best.json filename.")],
    best_b: Annotated[str, typer.Argument(help="Second best.json filename.")],
    ledger_root: Annotated[Path, typer.Option(help="Optimization ledger root.")] = Path("ledger"),
) -> None:
    """Side-by-side diff of two selection outputs for the same opt_id."""
    opt_dir = opt_dir_for(ledger_root, opt_id)
    a_path, b_path = opt_dir / best_a, opt_dir / best_b
    if not a_path.exists() or not b_path.exists():
        typer.echo(f"missing best file(s) under {opt_dir}", err=True)
        raise typer.Exit(code=1)
    a = json.loads(a_path.read_text())
    b = json.loads(b_path.read_text())
    lines = [f"comparing {best_a} vs {best_b}"]
    for key in ("decision", "pbo", "would_have_picked"):
        av = a.get(key)
        bv = b.get(key)
        marker = "=" if av == bv else "≠"
        lines.append(f"  {marker} {key}:")
        lines.append(f"      a: {json.dumps(av, sort_keys=True)}")
        lines.append(f"      b: {json.dumps(bv, sort_keys=True)}")
    a_final = (a.get("final") or {}).get("params")
    b_final = (b.get("final") or {}).get("params")
    lines.append(f"  final.params (a): {json.dumps(a_final, sort_keys=True)}")
    lines.append(f"  final.params (b): {json.dumps(b_final, sort_keys=True)}")
    typer.echo("\n".join(lines))


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


def _unimplemented(name: str) -> int:
    typer.echo(
        f"`{name}` is not implemented yet (drive the underlying surface via Python).",
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
