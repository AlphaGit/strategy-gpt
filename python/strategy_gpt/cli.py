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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

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
def hypothesize(  # noqa: PLR0913 — surface mirrors the workflow knobs
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
    dry_run: Annotated[
        bool,
        typer.Option(help="Validate inputs and exit without invoking the workflow."),
    ] = False,
) -> None:
    """Hypothesis loop entry — runs the multi-stage emission + evaluation flow.

    The command resolves the per-strategy ledger, loads or computes the
    baseline-best result, compiles the LangGraph workflow, invokes it,
    and prints the result summary as JSON to stdout. Persistence under
    ``ledger/strategies/<strategy>/`` is on by default; pass
    ``--dry-run`` to validate inputs without invoking the workflow.

    The full collaborator wiring (KB, reasoning client, build pipeline,
    engine evaluator) is constructed inside this command from the
    environment. Tests drive the orchestrator directly via
    :func:`strategy_gpt.hypothesize.hypothesize` with stubs.
    """
    if baseline_from is not None and baseline_defaults:
        msg = "--baseline-from and --baseline-defaults are mutually exclusive"
        raise typer.BadParameter(msg)

    summary = {
        "strategy": strategy,
        "ledger_root": str(ledger_root),
        "baseline_from": baseline_from,
        "baseline_defaults": baseline_defaults,
        "max_backtests": max_backtests,
        "quick": quick,
        "borderline_k": borderline_k,
        "k_candidates": k_candidates,
        "iteration_budget": iteration_budget,
    }
    if dry_run:
        typer.echo(json.dumps({"dry_run": True, "resolved": summary}, indent=2))
        return

    typer.echo(
        json.dumps(
            {
                "status": "wiring_incomplete",
                "message": (
                    "hypothesize CLI is wired through to the workflow but the "
                    "engine + KB collaborator construction is operator-specific "
                    "and not finalized in Phase D. Drive via "
                    "`strategy_gpt.hypothesize.hypothesize` from Python with a "
                    "fully populated HypothesizeDeps."
                ),
                "resolved": summary,
            },
            indent=2,
        )
    )
    raise typer.Exit(code=0)


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


def _cli_repair_menu(exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
    """Interactive prompt for the four repair-exhaustion options.

    Always reads from stdin via ``typer.prompt`` so the CLI integrates
    naturally with the rest of the author session. Tests inject a
    different ``repair_menu`` callable directly into ``run_author_session``.
    """
    del exc
    typer.echo("")
    typer.echo("Repair budget exhausted. Choose how to proceed:")
    typer.echo("  1) Suggest an alternative approach in natural language")
    typer.echo("  2) Retry with an extended repair budget")
    typer.echo("  3) Edit a specific decision (mechanism, params, smoke, …)")
    typer.echo("  4) Abort")
    choice = typer.prompt("Choice [1-4]", default="4").strip()
    if choice == "1":
        guidance = typer.prompt("Describe the alternative approach")
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
        guidance = typer.prompt("How should it change?")
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

    def render(event: AuthorEvent) -> None:  # noqa: PLR0912 — one branch per event type
        match event:
            case RepairAttemptStarted(attempt=a, budget=b):
                typer.echo(f"[attempt {a + 1}/{b + 1}] starting emit/build/smoke")
            case RepairAttemptCompleted(attempt=a, outcome=o):
                typer.echo(f"[attempt {a + 1}] result: {o}")
            case FileWritten(path=p):
                if verbose:
                    typer.echo(f"  wrote {p}")
            case LintStarted():
                typer.echo("  lint …", nl=False)
            case LintCompleted(ok=ok):
                typer.echo(" ok" if ok else " rejected")
            case CargoBuildStarted(args=args):
                if verbose:
                    typer.echo(f"  cargo build (argv={args!r}) …")
                else:
                    typer.echo("  cargo build …", nl=False)
            case CargoBuildCompleted(returncode=rc, duration_seconds=d):
                if verbose:
                    typer.echo(f"  cargo build returncode={rc} in {d:.2f}s")
                else:
                    typer.echo(f" {'done' if rc == 0 else 'failed'} in {d:.2f}s")
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
    build_pipeline = BuildPipeline(
        cache_root=cache_root,
        work_root=work_root,
        engine_rt_path=crates_dir / "engine-rt",
        whitelist_path=crates_dir / "build-pipeline" / "whitelist.toml",
    )

    from .author_decisions import DecisionRecord  # noqa: PLC0415
    from .author_events import noop_sink  # noqa: PLC0415

    event_sink = (
        noop_sink if quiet else _cli_event_renderer(verbose=verbose)
    )
    opened_record: list[DecisionRecord] = []
    try:
        intent = run_intent_dialog(
            seed=idea,
            reasoning_client=reasoning_client,
            crates_dir=crates_dir,
            model_name=model or "default",
            on_record_ready=opened_record.append,
            quiet=quiet,
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
