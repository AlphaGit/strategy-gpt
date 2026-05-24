"""Construction helpers for the ``strategy-gpt hypothesize`` CLI.

The orchestrator entry :func:`strategy_gpt.hypothesize.hypothesize` accepts
a fully-populated :class:`~strategy_gpt.hypothesize.HypothesizeDeps`. Each
collaborator (KB client, stage reasoning client, build pipeline,
evaluate-fold callable, baseline tuple) is operator-specific. This module
extracts the construction policy out of ``cli.py`` so unit tests can
exercise each helper without booting typer.

Prerequisites for the KB ingestion path:

- ``kb/sources.toml`` exists and lists curated sources.
- Source files referenced under ``kb/`` exist (the starter corpus ships
  under ``kb/starter/``).

The KB store path defaults to ``kb/store/``; first-build ingests the
corpus and prints a one-time progress banner. Subsequent runs reuse the
persisted store.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover
    from .prompts import StagePrompt

from .author import load_intent_toml
from .build_pipeline import (
    StrategyManifest,
    _BuildPipelineLike,
)
from .engine import Engine
from .gateway import Gateway
from .kb import KnowledgeBase
from .optimization_ledger import opt_dir_for, read_best, read_manifest
from .optimizer import ContinuousParam, IntParam, RandomParam
from .reasoning import ReasoningModel, select_reasoning_model
from .reasoning_clients import (
    DispatchReasoningClient,
    StageReasoningClient,
    build_dispatch_client,
)
from .tester import EvaluateFoldFn
from .types import (
    AdjustmentPolicy,
    BacktestMetrics,
    BacktestResult,
    Bar,
    BarRequest,
    Resolution,
    ResultMeta,
    RunnerVersion,
)

# ---------------------------------------------------------------------------
# Stage routing
# ---------------------------------------------------------------------------

Stage = Literal[1, 2, 3]

StageName = Literal["stage1", "stage2", "stage3", "critique", "rank"]

_STAGE_NAMES: tuple[StageName, ...] = ("stage1", "stage2", "stage3", "critique", "rank")

_STAGE_TO_NAME: dict[int, StageName] = {1: "stage1", 2: "stage2", 3: "stage3"}


class _StageRouter:
    """Per-stage model routing wrapper.

    Holds a :class:`DispatchReasoningClient` plus a stage→model map. On
    ``emit_stage`` the caller-supplied ``model`` argument is overridden
    by the per-stage entry so each stage can target a different model
    without changing the workflow's signature. Stages without an entry
    fall back to the workflow-supplied ``model``.
    """

    def __init__(
        self,
        *,
        dispatch: DispatchReasoningClient,
        stage_models: Mapping[StageName, ReasoningModel],
    ) -> None:
        self._dispatch = dispatch
        self._stage_models = dict(stage_models)

    @property
    def stage_models(self) -> Mapping[StageName, ReasoningModel]:
        return dict(self._stage_models)

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: Stage,
        model: ReasoningModel,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str:
        name = _STAGE_TO_NAME.get(stage)
        effective = self._stage_models.get(name, model) if name is not None else model
        return self._dispatch.emit_stage(
            prompt=prompt,
            stage=stage,
            model=effective,
            max_tokens=max_tokens,
            temperature=temperature,
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WiringError(RuntimeError):
    """Raised when wiring inputs are missing or malformed.

    Distinct from :class:`RuntimeError` so the CLI translation layer can
    map these to typer.Exit with a clean message rather than spilling
    stack traces.
    """


class MissingArtifactError(WiringError):
    """The crate exists but a required artifact is missing."""


class MissingApiKeyError(WiringError):
    """Neither ``ANTHROPIC_API_KEY`` nor ``OPENAI_API_KEY`` is set."""


class MissingOptimizeRunError(WiringError):
    """Referenced optimize-run id was not found in the ledger."""


# ---------------------------------------------------------------------------
# Crate paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CratePaths:
    """Resolved on-disk locations for an authored strategy crate.

    ``experiment_yaml`` is optional: a freshly authored crate may have
    only ``smoke.toml``; the evaluate-fold factory honours that case by
    falling back to a single-fold evaluator.
    """

    crate_dir: Path
    cargo_toml: Path
    lib_rs: Path
    intent_toml: Path
    smoke_toml: Path
    experiment_yaml: Path | None


def resolve_crate_paths(strategy_name: str, crates_dir: Path) -> CratePaths:
    """Return absolute paths for the named crate's required artifacts.

    Convention: ``crates/<name>-strategy/`` per :func:`author.crate_dir_for`.
    Missing crate dir raises :class:`MissingArtifactError`; the message
    names the missing artifact and the suggested next step.
    """
    crate_dir = (crates_dir / f"{strategy_name}-strategy").resolve()
    if not crate_dir.is_dir():
        msg = (
            f"crate directory {crate_dir} does not exist; "
            f"run 'strategy-gpt author {strategy_name}' first"
        )
        raise MissingArtifactError(msg)

    cargo = crate_dir / "Cargo.toml"
    lib = crate_dir / "src" / "lib.rs"
    intent = crate_dir / "intent.toml"
    smoke = crate_dir / "smoke.toml"
    experiment = crate_dir / "experiment.yaml"

    for label, path in (
        ("Cargo.toml", cargo),
        ("src/lib.rs", lib),
        ("intent.toml", intent),
        ("smoke.toml", smoke),
    ):
        if not path.is_file():
            msg = (
                f"{path} not found; the strategy crate has not been "
                f"authored cleanly (missing {label})"
            )
            raise MissingArtifactError(msg)

    return CratePaths(
        crate_dir=crate_dir,
        cargo_toml=cargo,
        lib_rs=lib,
        intent_toml=intent,
        smoke_toml=smoke,
        experiment_yaml=experiment if experiment.is_file() else None,
    )


# ---------------------------------------------------------------------------
# API-key guard
# ---------------------------------------------------------------------------


def verify_api_keys(env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`MissingApiKeyError` when no LLM provider key is set.

    Surfaces the same shape the dispatch client checks for so the CLI
    can fail before constructing collaborators that would each raise
    independently.
    """
    available = env if env is not None else os.environ
    if not (available.get("ANTHROPIC_API_KEY") or available.get("OPENAI_API_KEY")):
        msg = "set ANTHROPIC_API_KEY or OPENAI_API_KEY before running hypothesize"
        raise MissingApiKeyError(msg)


# ---------------------------------------------------------------------------
# KB client
# ---------------------------------------------------------------------------

_KB_BANNER_PRINTED = False


def build_kb_client(
    store_path: Path,
    sources_path: Path,
    *,
    rebuild: bool = False,
    banner: Callable[[str], None] | None = None,
) -> KnowledgeBase:
    """Construct a :class:`KnowledgeBase` bound to ``store_path``.

    When the store doesn't exist (or ``rebuild=True``), reads
    ``sources_path`` and triggers a one-shot ingestion. ``banner`` is
    invoked once for the first-build path so the CLI can surface the
    expected wait; a no-op default keeps tests quiet.
    """
    global _KB_BANNER_PRINTED  # noqa: PLW0603 — one-shot banner is intentional
    store_path = store_path.resolve()
    base_dir = sources_path.parent.resolve()
    db_path = store_path / "kb.sqlite"

    needs_build = rebuild or not db_path.exists()
    if needs_build:
        if not sources_path.is_file():
            msg = f"KB sources file {sources_path} not found"
            raise MissingArtifactError(msg)
        store_path.mkdir(parents=True, exist_ok=True)
        if banner is not None and not _KB_BANNER_PRINTED:
            banner(f"building KB store at {store_path} from {sources_path} (one-time ingestion)")
            _KB_BANNER_PRINTED = True
        kb = KnowledgeBase(db_path, base_dir)
        kb.reingest(sources_path.read_text(encoding="utf-8"))
        return kb
    return KnowledgeBase(db_path, base_dir)


# ---------------------------------------------------------------------------
# Stage reasoning client
# ---------------------------------------------------------------------------


def build_stage_client(
    model_overrides: Mapping[StageName, str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> StageReasoningClient:
    """Build a :class:`StageReasoningClient` with per-stage models.

    Resolves the environment-default reasoning model for unset stages
    and applies ``model_overrides`` per stage. Provider is inferred from
    the model id prefix (``claude*`` → anthropic, else openai).
    """
    available = env if env is not None else os.environ
    dispatch = build_dispatch_client(env=dict(available))
    default_model = select_reasoning_model(env=dict(available))

    overrides = model_overrides or {}
    stage_models: dict[StageName, ReasoningModel] = {}
    for stage in _STAGE_NAMES:
        override_id = overrides.get(stage)
        if override_id is not None:
            provider: Literal["anthropic", "openai"] = (
                "anthropic" if override_id.startswith("claude") else "openai"
            )
            stage_models[stage] = ReasoningModel(provider=provider, model_id=override_id)
        else:
            stage_models[stage] = default_model
    return _StageRouter(dispatch=dispatch, stage_models=stage_models)


# ---------------------------------------------------------------------------
# Evaluate-fold factory
# ---------------------------------------------------------------------------


_DEFAULT_POLL_INTERVAL = 0.05


def _build_strategy_library(
    crate_paths: CratePaths,
    build_pipeline: _BuildPipelineLike,
) -> str:
    """Build the strategy crate via the pipeline and return its library path."""
    src = crate_paths.lib_rs.read_text(encoding="utf-8")
    manifest_text = crate_paths.cargo_toml.read_text(encoding="utf-8")
    manifest = _parse_manifest(manifest_text)
    outcome = build_pipeline.build(src, manifest)
    return outcome.artifact.library_path


def _parse_manifest(text: str) -> StrategyManifest:
    """Extract a :class:`StrategyManifest` from a Cargo.toml text."""
    import tomllib  # noqa: PLC0415 — stdlib, lazy

    data = tomllib.loads(text)
    pkg = data.get("package", {})
    name = pkg.get("name", "strategy")
    version = pkg.get("version", "0.1.0")

    from .build_pipeline import ManifestDep  # noqa: PLC0415

    def _deps(section: object) -> list[ManifestDep]:
        if not isinstance(section, dict):
            return []
        out: list[ManifestDep] = []
        for dep_name, spec in section.items():
            if isinstance(spec, str):
                req: Any = spec
            elif isinstance(spec, dict):
                req = spec.get("version", "*")
            else:
                req = "*"
            out.append(ManifestDep(name=str(dep_name), req=str(req)))
        return out

    return StrategyManifest(
        name=str(name),
        version=str(version),
        dependencies=_deps(data.get("dependencies")),
        dev_dependencies=_deps(data.get("dev-dependencies")),
        build_dependencies=_deps(data.get("build-dependencies")),
    )


def _bar_request_from_smoke(smoke_data: Mapping[str, Any]) -> BarRequest:
    """Project a parsed smoke.toml into a :class:`BarRequest`."""
    from datetime import UTC, datetime  # noqa: PLC0415

    spec = smoke_data.get("smoke_spec", smoke_data)
    start = datetime.fromisoformat(str(spec["start"])).replace(tzinfo=UTC)
    end = datetime.fromisoformat(str(spec["end"])).replace(tzinfo=UTC)
    return BarRequest(
        provider=str(spec.get("provider", "yfinance")),
        symbol=str(spec["symbol"]),
        start=start,
        end=end,
        resolution=Resolution(str(spec.get("resolution", "1d"))),
        adjustment=AdjustmentPolicy.BACK_ADJUSTED,
    )


def _make_engine(engine_worker_path: Path) -> Engine:
    if not engine_worker_path.is_file():
        msg = (
            f"engine-worker binary not found at {engine_worker_path}; "
            "build it via 'cd crates && cargo build -p engine-worker'"
        )
        raise MissingArtifactError(msg)
    return Engine(engine_worker_path)


def _submit_and_extract_metrics(
    engine: Engine,
    *,
    library_path: str,
    bars: list[Bar],
    spec: dict[str, Any],
    dataset_manifest: str,
) -> BacktestMetrics:
    """Submit a single-run batch, poll, and project to :class:`BacktestMetrics`."""
    handle = engine.submit_batch(library_path, bars, spec, dataset_manifest)
    while True:
        status = engine.poll(handle)
        if status.status in ("completed", "failed", "cancelled"):
            break
        time.sleep(_DEFAULT_POLL_INTERVAL)
    if status.status != "completed":
        msg = f"engine batch terminated with status={status.status}: {status.error or ''}"
        raise RuntimeError(msg)
    results = status.results or []
    if not results:
        msg = "engine batch produced no result entries"
        raise RuntimeError(msg)
    entry = results[0]
    if entry.get("status") != "ok":
        msg = f"engine run failed: {entry.get('error_kind')}: {entry.get('message')}"
        raise RuntimeError(msg)
    raw_metrics = entry["result"].get("metrics") or {}
    return BacktestMetrics.model_validate(raw_metrics)


EvaluateFoldFactory = Callable[[str], EvaluateFoldFn]
"""Factory: ``(library_path) -> EvaluateFoldFn``.

Each candidate hypothesis compiles to a DIFFERENT shared library; the
mini-optimize search MUST run that library, not the baseline's, or every
candidate scores identically and the gate falsely rejects every change.
The factory binds a library path at the point the candidate's build
artifact becomes available."""


def build_evaluate_fold(
    crate_paths: CratePaths,
    *,
    build_pipeline: _BuildPipelineLike,
    engine_worker_path: Path,
    gateway_root: Path,
    quick_fold_count: int | None = None,
) -> tuple[EvaluateFoldFactory, EvaluateFoldFn, str, int]:
    """Build an evaluate-fold factory + baseline evaluator pair.

    Returns ``(factory, baseline_evaluator, dataset_manifest_hash,
    fold_count)``. The factory takes a library path and returns an
    :class:`EvaluateFoldFn` bound to that artifact. The baseline
    evaluator is the factory applied to the baseline crate's compiled
    library — used by ``baseline_defaults`` to compute per-fold
    baseline metrics.

    When the crate carries an ``experiment.yaml``, fold count comes
    from the embedded ``folds`` block; otherwise the smoke window
    becomes a single-fold evaluator (fold 0 only).

    ``quick_fold_count`` caps the fold count regardless of source (used
    by ``--quick`` to keep the loop cheap).
    """
    baseline_library_path = _build_strategy_library(crate_paths, build_pipeline)
    engine = _make_engine(engine_worker_path)

    import tomllib  # noqa: PLC0415

    smoke_data = tomllib.loads(crate_paths.smoke_toml.read_text(encoding="utf-8"))
    smoke_request = _bar_request_from_smoke(smoke_data)
    gw = Gateway(gateway_root)
    if smoke_request.provider == "yfinance":
        gw.register_yfinance_provider(smoke_request.provider)
    response = gw.fetch(smoke_request, "prefer_cache")
    bars = list(response.bars)
    dataset_manifest = response.manifest_hash

    fold_slices: list[tuple[Any, Any]]
    if crate_paths.experiment_yaml is not None:
        fold_slices = _derive_fold_slices_from_experiment(crate_paths.experiment_yaml)
    else:
        fold_slices = [(smoke_request.start, smoke_request.end)]

    if quick_fold_count is not None and quick_fold_count > 0:
        fold_slices = fold_slices[:quick_fold_count]

    fold_count = len(fold_slices)
    strategy_name = crate_paths.crate_dir.name.removesuffix("-strategy")

    def factory(library_path: str) -> EvaluateFoldFn:
        def _evaluator(params: Mapping[str, Any], fold_idx: int) -> BacktestMetrics:
            if fold_idx < 0 or fold_idx >= fold_count:
                msg = f"fold_idx {fold_idx} out of range [0, {fold_count})"
                raise IndexError(msg)
            start, end = fold_slices[fold_idx]
            run = {
                "params": dict(params),
                "modes": [{"kind": "plain"}],
                "seed": 0,
                "slice": {"start": start.isoformat(), "end": end.isoformat()},
            }
            spec = {
                "strategy": strategy_name,
                "dataset": dataset_manifest,
                "runs": [run],
                "engine": {
                    "fill_model": "NextBarOpen",
                    "initial_capital": 100_000.0,
                    "commission_per_fill": 0.0,
                    "slippage_bps": 0.0,
                    "sanity": {"max_intent_size": 1e9, "max_position_size": 1e9},
                },
                "parallelism": 1,
                "failure_mode": "continue",
            }
            return _submit_and_extract_metrics(
                engine,
                library_path=library_path,
                bars=bars,
                spec=spec,
                dataset_manifest=dataset_manifest,
            )

        return _evaluator

    return factory, factory(baseline_library_path), dataset_manifest, fold_count


def _derive_fold_slices_from_experiment(experiment_yaml: Path) -> list[tuple[Any, Any]]:
    """Derive (train_start, train_end) pairs from an experiment.yaml.

    Uses :func:`folds.derive_folds` over the spec's first run slice so
    the fold boundaries match what a future ``optimize`` invocation
    would see. When the spec carries no ``folds`` block, fall back to
    the run's slice as a single fold.
    """
    from . import experiment_spec as espec  # noqa: PLC0415
    from .folds import derive_folds  # noqa: PLC0415

    parsed = espec.load(experiment_yaml)
    if not parsed.runs:
        msg = f"experiment.yaml at {experiment_yaml} has no runs"
        raise WiringError(msg)
    base = parsed.runs[0].slice
    if parsed.folds is None:
        return [(base.start, base.end)]
    folds = derive_folds(base, parsed.folds)
    return [(f.train.start, f.train.end) for f in folds]


# ---------------------------------------------------------------------------
# Baseline tuple
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineTuple:
    """Materialized baseline carried into :class:`HypothesizeDeps`.

    ``source`` labels how the baseline was resolved so the CLI summary
    and per-strategy ledger can surface it.
    """

    result: BacktestResult
    files: Mapping[str, str]
    params_schema: dict[str, Any] | None
    per_fold_scores: Sequence[float]
    metrics: Mapping[str, float]
    aggregate_score: float
    source: str


# ---------------------------------------------------------------------------
# Baseline loaders
# ---------------------------------------------------------------------------


def load_baseline_from_optimize(
    opt_run_id: str,
    ledger_root: Path,
    *,
    crate_paths: CratePaths | None = None,
    objective_metric: str = "sharpe",
) -> BaselineTuple:
    """Load a baseline tuple from an optimize-run row.

    Reads ``best.json``, the per-fold cross-validation metrics, and the
    primary metric per fold. Files are loaded from the crate's current
    source bundle when ``crate_paths`` is supplied; this keeps the
    baseline-source label honest even when the optimize run didn't
    persist its source set.
    """
    opt_dir = opt_dir_for(ledger_root, opt_run_id)
    if not opt_dir.exists():
        msg = f"optimize-run {opt_run_id!r} not found under {opt_dir}"
        raise MissingOptimizeRunError(msg)

    best = read_best(opt_dir)
    if best is None:
        msg = f"optimize-run {opt_run_id!r}: no best.json under {opt_dir}"
        raise MissingArtifactError(msg)

    final = best.get("final")
    if final is None:
        msg = (
            f"optimize-run {opt_run_id!r}: best.json has no `final` block "
            "(no candidate passed the objective)"
        )
        raise MissingArtifactError(msg)

    aggregate_metrics_raw = final.get("aggregate_metrics") or {}
    aggregate_metrics = {
        k: float(v) for k, v in aggregate_metrics_raw.items() if isinstance(v, (int, float))
    }
    aggregate_score = float(final.get("aggregate_score", 0.0))

    oos_metrics_per_fold = final.get("oos_metrics") or []
    per_fold_scores: list[float] = []
    for fold_metrics in oos_metrics_per_fold:
        if not isinstance(fold_metrics, Mapping):
            continue
        per_fold_scores.append(float(fold_metrics.get(objective_metric, 0.0)))
    if not per_fold_scores:
        per_fold_scores = [aggregate_score]

    manifest = read_manifest(opt_dir)
    dataset_manifest = str(manifest.get("dataset_manifest", ""))
    artifact_path = str(manifest.get("artifact_path", ""))

    files: dict[str, str] = {}
    params_schema: dict[str, Any] | None = None
    if crate_paths is not None:
        files = _read_crate_source_bundle(crate_paths)
        params_schema = _maybe_read_params_schema(crate_paths)

    metrics_model = _aggregate_to_backtest_metrics(aggregate_metrics)
    result = _synthetic_backtest_result(
        strategy_artifact=artifact_path or "optimize-baseline",
        dataset_manifest=dataset_manifest or opt_run_id,
        metrics=metrics_model,
    )
    return BaselineTuple(
        result=result,
        files=files,
        params_schema=params_schema,
        per_fold_scores=per_fold_scores,
        metrics=aggregate_metrics,
        aggregate_score=aggregate_score,
        source=f"optimize_run:{opt_run_id}",
    )


def compute_baseline_defaults(
    crate_paths: CratePaths,
    evaluate_fold: EvaluateFoldFn,
    fold_count: int,
    *,
    objective_metric: str = "sharpe",
    progress_sink: Callable[[str], None] | None = None,
) -> BaselineTuple:
    """Build a baseline by invoking ``evaluate_fold`` at the crate's defaults.

    Default parameter values come from
    ``intent.toml.param_schema_sketch``: every entry whose value is a
    mapping with a ``"default"`` key contributes its default. Parameters
    without a default are omitted (the strategy is expected to use its
    own coded default).

    ``progress_sink`` receives per-fold heartbeat lines so the operator
    sees what the baseline is producing while strategies execute (each
    fold spawns an engine subprocess, which can take seconds to
    minutes).
    """
    intent = load_intent_toml(crate_paths.crate_dir)
    defaults = _extract_param_defaults(intent.param_schema_sketch)

    per_fold_scores: list[float] = []
    last_metrics: BacktestMetrics | None = None
    for fold_idx in range(fold_count):
        if progress_sink is not None:
            progress_sink(
                f"baseline_defaults: running fold {fold_idx + 1}/{fold_count} "
                f"with defaults={defaults}..."
            )
        m = evaluate_fold(defaults, fold_idx)
        last_metrics = m
        score = float(getattr(m, objective_metric))
        per_fold_scores.append(score)
        if progress_sink is not None:
            progress_sink(
                f"baseline_defaults: fold {fold_idx + 1}/{fold_count} done "
                f"{objective_metric}={score:.4f} (sharpe={m.sharpe:.4f}, "
                f"trades={m.n_trades}, max_dd={m.max_drawdown:.2%}, "
                f"ann_ret={m.annualized_return:.2%})"
            )

    if last_metrics is None:
        msg = "compute_baseline_defaults: evaluate_fold was not invoked (fold_count=0)"
        raise WiringError(msg)

    aggregate = sum(per_fold_scores) / max(len(per_fold_scores), 1)
    files = _read_crate_source_bundle(crate_paths)
    params_schema = _maybe_read_params_schema(crate_paths)
    metrics_dict = {
        k: float(v) for k, v in last_metrics.model_dump().items() if isinstance(v, (int, float))
    }
    result = _synthetic_backtest_result(
        strategy_artifact=str(crate_paths.crate_dir),
        dataset_manifest=f"baseline-defaults:{crate_paths.crate_dir.name}",
        metrics=last_metrics,
    )
    return BaselineTuple(
        result=result,
        files=files,
        params_schema=params_schema,
        per_fold_scores=per_fold_scores,
        metrics=metrics_dict,
        aggregate_score=aggregate,
        source="baseline_defaults",
    )


def _read_crate_source_bundle(crate_paths: CratePaths) -> dict[str, str]:
    """Read the crate's source files into a path → content map.

    The bundle is what the per-strategy ledger persists under
    ``sources/<files_set_hash>/``; the keys mirror the conventional
    crate-relative paths.
    """
    out: dict[str, str] = {
        "Cargo.toml": crate_paths.cargo_toml.read_text(encoding="utf-8"),
        "src/lib.rs": crate_paths.lib_rs.read_text(encoding="utf-8"),
    }
    params_schema = crate_paths.crate_dir / "params_schema.json"
    if params_schema.is_file():
        out["params_schema.json"] = params_schema.read_text(encoding="utf-8")
    return out


def _maybe_read_params_schema(crate_paths: CratePaths) -> dict[str, Any] | None:
    path = crate_paths.crate_dir / "params_schema.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def _extract_param_defaults(param_schema_sketch: Mapping[str, Any]) -> dict[str, Any]:
    """Lift ``default`` values out of a param-schema sketch."""
    defaults: dict[str, Any] = {}
    for name, spec in param_schema_sketch.items():
        if isinstance(spec, Mapping) and "default" in spec:
            defaults[str(name)] = spec["default"]
    return defaults


def _aggregate_to_backtest_metrics(aggregate_metrics: Mapping[str, float]) -> BacktestMetrics:
    """Project a metric dict onto :class:`BacktestMetrics` with safe defaults."""
    return BacktestMetrics(
        sharpe=float(aggregate_metrics.get("sharpe", 0.0)),
        sortino=float(aggregate_metrics.get("sortino", 0.0)),
        profit_factor=float(aggregate_metrics.get("profit_factor", 1.0)),
        win_ratio=float(aggregate_metrics.get("win_ratio", 0.5)),
        max_drawdown=float(aggregate_metrics.get("max_drawdown", 0.0)),
        annualized_return=float(aggregate_metrics.get("annualized_return", 0.0)),
        n_trades=int(aggregate_metrics.get("n_trades", 0)),
        avg_trade_length_bars=float(aggregate_metrics.get("avg_trade_length_bars", 0.0)),
    )


def _synthetic_backtest_result(
    *,
    strategy_artifact: str,
    dataset_manifest: str,
    metrics: BacktestMetrics,
) -> BacktestResult:
    """Wrap a metrics block in a minimal :class:`BacktestResult`.

    The optimize ledger doesn't persist full :class:`BacktestResult`
    payloads (trades, signals, equity, regimes); the baseline loader
    synthesises a result that the ``diagnose`` node can read without
    crashing. Downstream diagnosis quality is bounded by what's
    actually present; empty trade/signal/regime lists are honest.
    """
    meta = ResultMeta(
        strategy_artifact=strategy_artifact,
        dataset_manifest=dataset_manifest,
        seed=0,
        runner_version=RunnerVersion(major=0, minor=1, patch=0),
    )
    return BacktestResult(
        meta=meta,
        metrics=metrics,
        trades=[],
        signals=[],
        equity=[],
        exec_log=[],
        regimes=[],
    )


# ---------------------------------------------------------------------------
# kept_bounds + objective resolution
# ---------------------------------------------------------------------------


def resolve_kept_bounds(intent_toml_data: Mapping[str, Any]) -> dict[str, RandomParam]:
    """Project ``param_schema_sketch`` entries into optimizer bound primitives.

    The mini-optimize search space requires concrete (min, max) ranges
    for every ``kept`` parameter. Entries that don't supply min/max are
    omitted; the workflow surfaces a missing-bound as a search-space
    construction error so the operator can fix the intent.
    """
    schema = intent_toml_data.get("param_schema_sketch", intent_toml_data)
    if not isinstance(schema, Mapping):
        return {}
    bounds: dict[str, RandomParam] = {}
    for name, spec in schema.items():
        if not isinstance(spec, Mapping):
            continue
        if "min" not in spec or "max" not in spec:
            continue
        kind = str(spec.get("kind", "f64"))
        try:
            if kind == "i64":
                bounds[str(name)] = IntParam(low=int(spec["min"]), high=int(spec["max"]))
            else:
                bounds[str(name)] = ContinuousParam(
                    low=float(spec["min"]),
                    high=float(spec["max"]),
                )
        except (TypeError, ValueError):
            continue
    return bounds


_FALLBACK_OBJECTIVE_METRIC = "sharpe"


def resolve_objective_metric(
    intent_toml_data: Mapping[str, Any],
    override: str | None,
) -> str:
    """Pick the objective metric: CLI override > intent.toml > fallback.

    ``intent.toml.objective_metric`` is honoured when present (forward-
    compatible: the author surface does not emit one today). The
    fallback ``"sharpe"`` matches the field on :class:`BacktestMetrics`.
    """
    if override:
        return override
    val = intent_toml_data.get("objective_metric")
    if isinstance(val, str) and val:
        return val
    return _FALLBACK_OBJECTIVE_METRIC


__all__ = [
    "BaselineTuple",
    "CratePaths",
    "EvaluateFoldFactory",
    "MissingApiKeyError",
    "MissingArtifactError",
    "MissingOptimizeRunError",
    "StageName",
    "WiringError",
    "build_evaluate_fold",
    "build_kb_client",
    "build_stage_client",
    "compute_baseline_defaults",
    "load_baseline_from_optimize",
    "resolve_crate_paths",
    "resolve_kept_bounds",
    "resolve_objective_metric",
    "verify_api_keys",
]
