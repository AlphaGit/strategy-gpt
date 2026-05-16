"""Per-fold optimization orchestrator.

Drives a single optimization run:

1. Derive folds from the experiment-spec ``folds`` block.
2. For each fold, run the configured search method against the fold's
   *train* slice as a sequence of packed engine batches (one per
   recursive-grid round; one for grid/random; ``n_iter`` sequential
   submissions for TPE).
3. Cross-validate every fold winner across every fold's *OOS* slice in
   one additional packed batch.
4. Score the per-candidate OOS aggregate via the strategy's objective
   spec (``aggregator: mean`` for v1) and pick the best; break ties by
   lower per-fold OOS-score variance.

The orchestrator is engine-IO-aware (knows how to pack a
:class:`BatchSpec`, submit it, poll until completion, classify per-run
results). The search algorithms themselves live in
:mod:`strategy_gpt.optimizer` and remain feedback-free / batch-shaped.
"""

from __future__ import annotations

import itertools
import json
import statistics
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .engine import Engine, JobStatus
from .experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from .experiment_spec import (
    ExperimentSpec,
    OptimizeBlock,
    RecursiveGridKnobs,
    RunConfig,
)
from .experiment_spec import (
    FloatParam as SpecFloatParam,
)
from .experiment_spec import (
    IntParam as SpecIntParam,
)
from .folds import FoldRange, derive_folds
from .objectives import evaluate_spec
from .optimizer import (
    ChoiceParam as OptChoiceParam,
)
from .optimizer import (
    ContinuousParam,
    GridSearcher,
    RandomParam,
    RandomSearcher,
    RecursiveGridDriver,
    RecursiveGridSearcher,
    Trial,
)
from .optimizer import (
    IntParam as OptIntParam,
)
from .types import Bar, EvaluationOutcome, TimeRange
from .types import EngineConfig as LedgerEngineConfig

ParamSet = dict[str, Any]
MetricsDict = dict[str, float]

_TIE_EPSILON = 1e-6


@dataclass(frozen=True)
class TrialRow:
    """One row of ``trials.parquet`` (or the in-memory equivalent).

    Schema mirrors ``design.md §6`` of the ``optimize-command`` change.
    """

    trial_id: int
    round: int
    phase: str
    fold_index: int
    params: ParamSet
    seed: int
    metrics: MetricsDict
    score: float
    accepted: bool
    reject_reason: str
    wall_secs: float


@dataclass(frozen=True)
class FoldWinner:
    """A winning candidate for one fold's train-side search."""

    fold_index: int
    params: ParamSet
    train_metrics: MetricsDict
    train_score: float


@dataclass(frozen=True)
class CrossValidationOutcome:
    """One fold-winner's cross-validation result across all OOS slices."""

    fold_index: int
    params: ParamSet
    oos_metrics: list[MetricsDict]
    aggregate_metrics: MetricsDict
    aggregate_score: float
    aggregate_accepted: bool
    aggregate_reject_reason: str
    score_variance: float


@dataclass(frozen=True)
class OptimizationResult:
    """In-memory summary of a full optimization run."""

    opt_id: str
    started_at: datetime
    finished_at: datetime
    folds: list[FoldRange]
    fold_winners: list[FoldWinner]
    cross_validation: list[CrossValidationOutcome]
    final: CrossValidationOutcome | None
    trial_rows: list[TrialRow]
    resolved_parallelism: int
    seed: int


@dataclass
class _RunResultEntry:
    """Engine-side outcome for a single packed run."""

    metrics: MetricsDict
    wall_secs: float
    ok: bool
    error: str


def translate_space(
    space: Mapping[str, SpecFloatParam | SpecIntParam | SpecChoiceParam],
) -> dict[str, RandomParam]:
    """Translate experiment-spec param shapes into optimizer sampling primitives."""
    out: dict[str, RandomParam] = {}
    for name, p in space.items():
        if isinstance(p, SpecFloatParam):
            out[name] = ContinuousParam(low=float(p.low), high=float(p.high))
        elif isinstance(p, SpecIntParam):
            out[name] = OptIntParam(low=int(p.low), high=int(p.high))
        elif isinstance(p, SpecChoiceParam):
            out[name] = OptChoiceParam(choices=list(p.choices))
        else:  # pragma: no cover — pydantic union forbids other types.
            msg = f"translate_space: unsupported space entry for {name!r}: {type(p).__name__}"
            raise TypeError(msg)
    return out


def per_dim_resolutions(space: Mapping[str, Any]) -> dict[str, int]:
    """Extract per-dim ``resolution`` overrides for recursive grid."""
    out: dict[str, int] = {}
    for name, p in space.items():
        res = getattr(p, "resolution", None)
        if res is not None:
            out[name] = int(res)
    return out


_STEP_EPSILON = 1e-9


def _grid_values_for(  # noqa: PLR0911 — branches per spec shape + step presence.
    name: str,
    param: SpecFloatParam | SpecIntParam | SpecChoiceParam,
    default_resolution: int,
) -> list[Any]:
    """Build the discrete value list for ``method: grid``."""
    del name
    if isinstance(param, SpecChoiceParam):
        return list(param.choices)
    if isinstance(param, SpecIntParam):
        if param.step is not None:
            vals = list(range(param.low, param.high + 1, param.step))
            if vals[-1] != param.high:
                vals.append(param.high)
            return vals
        if default_resolution < 2:  # noqa: PLR2004
            return [param.low, param.high]
        out_ints: list[int] = []
        for i in range(default_resolution):
            v = round(param.low + (param.high - param.low) * i / (default_resolution - 1))
            if not out_ints or out_ints[-1] != v:
                out_ints.append(v)
        return out_ints
    # Float
    if param.step is not None:
        vals_f: list[float] = []
        current = float(param.low)
        while current <= param.high + 1e-12:
            vals_f.append(round(current, 12))
            current += param.step
        if vals_f and abs(vals_f[-1] - param.high) > _STEP_EPSILON:
            vals_f.append(param.high)
        return vals_f
    if default_resolution < 2:  # noqa: PLR2004
        return [param.low, param.high]
    return [
        param.low + (param.high - param.low) * i / (default_resolution - 1)
        for i in range(default_resolution)
    ]


def _build_engine_cfg(experiment: ExperimentSpec) -> dict[str, Any]:
    cfg = LedgerEngineConfig(
        fill_model=experiment.engine.fill_model,
        initial_capital=experiment.engine.initial_capital,
        commission_per_fill=experiment.engine.commission_per_fill,
        slippage_bps=0.0,
        sanity=experiment.engine.sanity,
    )
    parsed: dict[str, Any] = json.loads(cfg.model_dump_json())
    return parsed


def _build_run(template: RunConfig, params: ParamSet, slice_: TimeRange) -> dict[str, Any]:
    merged = {**template.params, **params}
    return {
        "params": merged,
        "modes": [dict(m) for m in template.modes],
        "seed": template.seed,
        "slice": {"start": slice_.start.isoformat(), "end": slice_.end.isoformat()},
    }


def _pack_batch(
    *,
    experiment: ExperimentSpec,
    dataset_manifest: str,
    runs: Sequence[dict[str, Any]],
    parallelism: int,
    failure_mode: str = "continue",
) -> dict[str, Any]:
    strategy = experiment.strategy_label or experiment.artifact.stem
    return {
        "strategy": strategy,
        "dataset": dataset_manifest,
        "runs": list(runs),
        "engine": _build_engine_cfg(experiment),
        "parallelism": parallelism,
        "failure_mode": failure_mode,
    }


def _submit_and_collect(  # noqa: PLR0913 — engine IO surface is wide.
    engine: Engine,
    *,
    artifact_path: Path,
    bars: list[Bar],
    spec: dict[str, Any],
    dataset_manifest: str,
    poll_interval_secs: float,
) -> list[_RunResultEntry]:
    """Submit a packed batch and return per-run :class:`_RunResultEntry`."""
    start = time.monotonic()
    handle = engine.submit_batch(artifact_path, bars, spec, dataset_manifest)
    try:
        while True:
            status: JobStatus = engine.poll(handle)
            if status.status in ("completed", "failed", "cancelled"):
                break
            time.sleep(poll_interval_secs)
    finally:
        pass
    total_wall = time.monotonic() - start
    n_runs = len(spec["runs"])
    avg_wall = total_wall / max(n_runs, 1)
    if status.status != "completed":
        # Whole batch failed (abort mode or transport-level failure). Surface
        # as N failed run entries so the searcher can score them as -inf.
        msg = status.error or f"batch terminated with status={status.status}"
        return [
            _RunResultEntry(metrics={}, wall_secs=avg_wall, ok=False, error=msg)
            for _ in range(n_runs)
        ]
    results_by_index: dict[int, _RunResultEntry] = {}
    raw = status.results or []
    for entry in raw:
        idx = int(entry["run_index"])
        if entry.get("status") == "ok":
            metrics_raw = entry["result"].get("metrics") or {}
            results_by_index[idx] = _RunResultEntry(
                metrics=dict(metrics_raw),
                wall_secs=avg_wall,
                ok=True,
                error="",
            )
        else:
            err_kind = entry.get("error_kind", "run_failed")
            err_msg = entry.get("message", "")
            results_by_index[idx] = _RunResultEntry(
                metrics={},
                wall_secs=avg_wall,
                ok=False,
                error=f"{err_kind}: {err_msg}".strip(": "),
            )
    return [
        results_by_index.get(
            i, _RunResultEntry(metrics={}, wall_secs=avg_wall, ok=False, error="missing_result")
        )
        for i in range(n_runs)
    ]


def _score(objective: Mapping[str, Any], metrics: Mapping[str, Any]) -> EvaluationOutcome:
    return evaluate_spec(dict(objective), dict(metrics))


_COUNT_METRICS: frozenset[str] = frozenset({"n_trades"})
"""Engine metrics that count discrete events. Aggregate as a sum so the
cross-fold value stays an integer (and carries the *total* across folds
rather than a fractional mean that no instrument can produce)."""


def _aggregate_mean(metrics_per_fold: Sequence[MetricsDict]) -> MetricsDict:
    """Aggregate metrics across folds.

    Continuous metrics (sharpe, sortino, profit_factor, …) aggregate by
    mean and stay precise as floats — rounding would discard signal.
    Count metrics (:data:`_COUNT_METRICS`, currently ``n_trades``)
    aggregate by sum, preserving their integer nature: a strategy
    can produce 5 trades in one fold and 7 in another, total 12;
    a mean of 6.0 is meaningless because no fold can fire half a trade.

    The aggregate *score* is computed in :func:`_cross_validate` from
    the per-fold objective outcomes (which always see the original
    integer-precise per-fold metrics), so the Rust evaluator is never
    asked to consume an aggregate that violates its typed schema.
    """
    if not metrics_per_fold:
        return {}
    keys: set[str] = set()
    for m in metrics_per_fold:
        keys.update(m.keys())
    out: MetricsDict = {}
    for k in keys:
        values: list[float] = []
        int_values: list[int] = []
        all_int = True
        for m in metrics_per_fold:
            v = m.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if _is_nan(float(v)):
                continue
            values.append(float(v))
            if isinstance(v, int):
                int_values.append(v)
            else:
                all_int = False
        if not values:
            continue
        if k in _COUNT_METRICS and all_int:
            out[k] = sum(int_values)
        else:
            out[k] = sum(values) / len(values)
    return out


def _is_nan(x: float) -> bool:
    return x != x  # noqa: PLR0124 — float NaN check.


def _build_recursive_grid(
    optim: OptimizeBlock, space: dict[str, RandomParam]
) -> RecursiveGridSearcher:
    knobs = optim.recursive_grid or RecursiveGridKnobs()
    return RecursiveGridSearcher(
        space=space,
        resolution=knobs.resolution,
        top_k=knobs.top_k,
        depth=knobs.depth,
        plateau_epsilon=knobs.plateau_epsilon,
        seed=optim.seed,
        per_dim_resolution=per_dim_resolutions(optim.space),
    )


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def run_optimization(  # noqa: PLR0913 — orchestrator carries the full IO surface.
    *,
    experiment: ExperimentSpec,
    objective: Mapping[str, Any],
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    opt_id: str,
    poll_interval_secs: float = 0.05,
    persist_writer: _PersistWriter | None = None,
) -> OptimizationResult:
    """Run a full optimization (per-fold train search + cross-fold OOS validation)."""
    if experiment.optimize is None or experiment.folds is None:
        msg = "run_optimization: experiment spec is missing `optimize` or `folds` block."
        raise ValueError(msg)
    if len(experiment.runs) != 1:
        msg = (
            "run_optimization: experiment must declare exactly one run template "
            f"(the optimizer overrides params per candidate); got {len(experiment.runs)}."
        )
        raise ValueError(msg)
    started_at = datetime.now(UTC)
    template = experiment.runs[0]
    folds = derive_folds(template.slice, experiment.folds)
    parallelism = experiment.resolved_parallelism()
    space = translate_space(experiment.optimize.space)

    trial_rows: list[TrialRow] = []
    trial_counter = itertools.count()
    fold_winners: list[FoldWinner] = []

    if persist_writer is not None:
        persist_writer.start(
            experiment=experiment,
            objective=objective,
            dataset_manifest=dataset_manifest,
            artifact_path=artifact_path,
            opt_id=opt_id,
            resolved_parallelism=parallelism,
            seed=experiment.optimize.seed,
            started_at=started_at,
            folds=folds,
        )

    for fold_index, fold in enumerate(folds):
        winner = _search_fold(
            experiment=experiment,
            objective=objective,
            engine=engine,
            artifact_path=artifact_path,
            bars=bars,
            dataset_manifest=dataset_manifest,
            template=template,
            optim=experiment.optimize,
            space=space,
            fold_index=fold_index,
            fold=fold,
            parallelism=parallelism,
            trial_counter=trial_counter,
            trial_rows=trial_rows,
            poll_interval_secs=poll_interval_secs,
            persist_writer=persist_writer,
        )
        fold_winners.append(winner)

    cross = _cross_validate(
        experiment=experiment,
        objective=objective,
        engine=engine,
        artifact_path=artifact_path,
        bars=bars,
        dataset_manifest=dataset_manifest,
        template=template,
        folds=folds,
        fold_winners=fold_winners,
        parallelism=parallelism,
        trial_counter=trial_counter,
        trial_rows=trial_rows,
        poll_interval_secs=poll_interval_secs,
        persist_writer=persist_writer,
    )

    final = _select_final(cross)
    finished_at = datetime.now(UTC)
    result = OptimizationResult(
        opt_id=opt_id,
        started_at=started_at,
        finished_at=finished_at,
        folds=list(folds),
        fold_winners=fold_winners,
        cross_validation=cross,
        final=final,
        trial_rows=trial_rows,
        resolved_parallelism=parallelism,
        seed=experiment.optimize.seed,
    )
    if persist_writer is not None:
        persist_writer.finish(result)
    return result


def _search_fold(  # noqa: PLR0913 — full IO surface.
    *,
    experiment: ExperimentSpec,
    objective: Mapping[str, Any],
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    template: RunConfig,
    optim: OptimizeBlock,
    space: dict[str, RandomParam],
    fold_index: int,
    fold: FoldRange,
    parallelism: int,
    trial_counter: Iterator[int],
    trial_rows: list[TrialRow],
    poll_interval_secs: float,
    persist_writer: _PersistWriter | None,
) -> FoldWinner:
    if optim.method == "recursive_grid":
        return _search_fold_recursive_grid(
            experiment=experiment,
            objective=objective,
            engine=engine,
            artifact_path=artifact_path,
            bars=bars,
            dataset_manifest=dataset_manifest,
            template=template,
            optim=optim,
            space=space,
            fold_index=fold_index,
            fold=fold,
            parallelism=parallelism,
            trial_counter=trial_counter,
            trial_rows=trial_rows,
            poll_interval_secs=poll_interval_secs,
            persist_writer=persist_writer,
        )
    return _search_fold_one_shot(
        experiment=experiment,
        objective=objective,
        engine=engine,
        artifact_path=artifact_path,
        bars=bars,
        dataset_manifest=dataset_manifest,
        template=template,
        optim=optim,
        space=space,
        fold_index=fold_index,
        fold=fold,
        parallelism=parallelism,
        trial_counter=trial_counter,
        trial_rows=trial_rows,
        poll_interval_secs=poll_interval_secs,
        persist_writer=persist_writer,
    )


def _search_fold_recursive_grid(  # noqa: PLR0913
    *,
    experiment: ExperimentSpec,
    objective: Mapping[str, Any],
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    template: RunConfig,
    optim: OptimizeBlock,
    space: dict[str, RandomParam],
    fold_index: int,
    fold: FoldRange,
    parallelism: int,
    trial_counter: Iterator[int],
    trial_rows: list[TrialRow],
    poll_interval_secs: float,
    persist_writer: _PersistWriter | None,
) -> FoldWinner:
    searcher = _build_recursive_grid(optim, space)
    driver = RecursiveGridDriver(searcher, salt=fold_index * 1_000_003)
    best: Trial | None = None
    best_params: ParamSet | None = None
    best_metrics: MetricsDict | None = None
    round_idx = 0
    while not driver.done:
        candidates = driver.candidates()
        if not candidates:
            break
        spec = _pack_batch(
            experiment=experiment,
            dataset_manifest=dataset_manifest,
            runs=[_build_run(template, c, fold.train) for c in candidates],
            parallelism=parallelism,
        )
        entries = _submit_and_collect(
            engine,
            artifact_path=artifact_path,
            bars=bars,
            spec=spec,
            dataset_manifest=dataset_manifest,
            poll_interval_secs=poll_interval_secs,
        )
        round_trials: list[Trial] = []
        for params, entry in zip(candidates, entries, strict=True):
            outcome = _score(objective, entry.metrics) if entry.ok else _failed_outcome(entry.error)
            accepted = outcome.accepted
            t = Trial(params=params, metrics=entry.metrics, outcome=outcome, accepted=accepted)
            round_trials.append(t)
            row = TrialRow(
                trial_id=next(trial_counter),
                round=round_idx,
                phase=f"train_fold_{fold_index}",
                fold_index=fold_index,
                params=params,
                seed=template.seed,
                metrics=entry.metrics,
                score=outcome.score,
                accepted=accepted,
                reject_reason="" if accepted else _reject_reason(entry, outcome),
                wall_secs=entry.wall_secs,
            )
            trial_rows.append(row)
            if persist_writer is not None:
                persist_writer.emit_row(row)
            if accepted and (best is None or outcome.score > best.outcome.score):
                best = t
                best_params = params
                best_metrics = entry.metrics
        driver.observe(round_trials)
        if persist_writer is not None:
            persist_writer.flush()
        round_idx += 1
    if best is None or best_params is None or best_metrics is None:
        # No accepted candidate; fall back to the highest-score trial in the
        # fold so the cross-validation phase still has a candidate to evaluate.
        fallback = _fallback_winner(trial_rows, fold_index)
        return fallback
    return FoldWinner(
        fold_index=fold_index,
        params=best_params,
        train_metrics=best_metrics,
        train_score=best.outcome.score,
    )


def _search_fold_one_shot(  # noqa: PLR0913
    *,
    experiment: ExperimentSpec,
    objective: Mapping[str, Any],
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    template: RunConfig,
    optim: OptimizeBlock,
    space: dict[str, RandomParam],
    fold_index: int,
    fold: FoldRange,
    parallelism: int,
    trial_counter: Iterator[int],
    trial_rows: list[TrialRow],
    poll_interval_secs: float,
    persist_writer: _PersistWriter | None,
) -> FoldWinner:
    candidates = list(_candidates_for_one_shot(optim, space))
    spec = _pack_batch(
        experiment=experiment,
        dataset_manifest=dataset_manifest,
        runs=[_build_run(template, c, fold.train) for c in candidates],
        parallelism=parallelism,
    )
    entries = _submit_and_collect(
        engine,
        artifact_path=artifact_path,
        bars=bars,
        spec=spec,
        dataset_manifest=dataset_manifest,
        poll_interval_secs=poll_interval_secs,
    )
    best: Trial | None = None
    best_params: ParamSet | None = None
    best_metrics: MetricsDict | None = None
    for params, entry in zip(candidates, entries, strict=True):
        outcome = _score(objective, entry.metrics) if entry.ok else _failed_outcome(entry.error)
        accepted = outcome.accepted
        row = TrialRow(
            trial_id=next(trial_counter),
            round=0,
            phase=f"train_fold_{fold_index}",
            fold_index=fold_index,
            params=params,
            seed=template.seed,
            metrics=entry.metrics,
            score=outcome.score,
            accepted=accepted,
            reject_reason="" if accepted else _reject_reason(entry, outcome),
            wall_secs=entry.wall_secs,
        )
        trial_rows.append(row)
        if persist_writer is not None:
            persist_writer.emit_row(row)
        t = Trial(params=params, metrics=entry.metrics, outcome=outcome, accepted=accepted)
        if accepted and (best is None or outcome.score > best.outcome.score):
            best = t
            best_params = params
            best_metrics = entry.metrics
    if persist_writer is not None:
        persist_writer.flush()
    if best is None or best_params is None or best_metrics is None:
        return _fallback_winner(trial_rows, fold_index)
    return FoldWinner(
        fold_index=fold_index,
        params=best_params,
        train_metrics=best_metrics,
        train_score=best.outcome.score,
    )


def _candidates_for_one_shot(
    optim: OptimizeBlock, space: dict[str, RandomParam]
) -> Iterable[ParamSet]:
    if optim.method == "grid":
        grid_knobs = optim.grid
        default_res = grid_knobs.resolution if grid_knobs and grid_knobs.resolution else 10
        keys = list(optim.space.keys())
        per_dim_values = [_grid_values_for(k, optim.space[k], default_res) for k in keys]
        grid = GridSearcher(grid=dict(zip(keys, per_dim_values, strict=True)))
        return grid.candidates()
    if optim.method == "random":
        random_knobs = optim.random
        assert random_knobs is not None  # noqa: S101 — pydantic-enforced.
        return RandomSearcher(
            space=space, n_iter=random_knobs.n_samples, seed=optim.seed
        ).candidates()
    if optim.method == "bayesian":
        # TPE needs sequential feedback; one-shot path is not appropriate.
        # The orchestrator currently does not implement the multi-round TPE
        # dispatch yet — surface explicitly.
        msg = (
            "run_optimization: method='bayesian' (TPE) is not yet wired into "
            "the per-fold orchestrator. Use `recursive_grid`, `grid`, or "
            "`random` for now."
        )
        raise NotImplementedError(msg)
    msg = f"run_optimization: unsupported optimize.method={optim.method!r}."
    raise ValueError(msg)


def _failed_outcome(error: str) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=False,
        score=float("-inf"),
        violations=[f"engine_error: {error}" if error else "engine_error"],
        soft_misses=[],
    )


def _reject_reason(entry: _RunResultEntry, outcome: EvaluationOutcome) -> str:
    if not entry.ok:
        return entry.error or "engine_error"
    if outcome.violations:
        return ",".join(outcome.violations)
    if outcome.soft_misses:
        return ",".join(outcome.soft_misses)
    return "below_gate"


def _fallback_winner(trial_rows: Sequence[TrialRow], fold_index: int) -> FoldWinner:
    """Pick the best-scoring trial in a fold when no candidate was accepted.

    Cross-validation still needs a candidate per fold to evaluate; rejection
    cascades through the OOS-aggregate scoring there.
    """
    fold_rows = [
        r for r in trial_rows if r.fold_index == fold_index and r.phase.startswith("train_fold_")
    ]
    if not fold_rows:
        return FoldWinner(
            fold_index=fold_index,
            params={},
            train_metrics={},
            train_score=float("-inf"),
        )
    best = max(fold_rows, key=lambda r: r.score)
    return FoldWinner(
        fold_index=fold_index,
        params=dict(best.params),
        train_metrics=dict(best.metrics),
        train_score=best.score,
    )


def _cross_validate(  # noqa: PLR0913
    *,
    experiment: ExperimentSpec,
    objective: Mapping[str, Any],
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    template: RunConfig,
    folds: Sequence[FoldRange],
    fold_winners: Sequence[FoldWinner],
    parallelism: int,
    trial_counter: Iterator[int],
    trial_rows: list[TrialRow],
    poll_interval_secs: float,
    persist_writer: _PersistWriter | None,
) -> list[CrossValidationOutcome]:
    """Run every fold winner across every fold's OOS slice in one packed batch."""
    runs: list[dict[str, Any]] = []
    plan: list[tuple[int, int]] = []  # (winner_index, fold_index)
    for w_idx, winner in enumerate(fold_winners):
        for f_idx, fold in enumerate(folds):
            runs.append(_build_run(template, winner.params, fold.oos))
            plan.append((w_idx, f_idx))
    if not runs:
        return []
    spec = _pack_batch(
        experiment=experiment,
        dataset_manifest=dataset_manifest,
        runs=runs,
        parallelism=parallelism,
    )
    entries = _submit_and_collect(
        engine,
        artifact_path=artifact_path,
        bars=bars,
        spec=spec,
        dataset_manifest=dataset_manifest,
        poll_interval_secs=poll_interval_secs,
    )
    per_winner_metrics: dict[int, list[MetricsDict]] = {i: [] for i in range(len(fold_winners))}
    per_winner_outcomes: dict[int, list[EvaluationOutcome]] = {
        i: [] for i in range(len(fold_winners))
    }
    for (w_idx, f_idx), entry in zip(plan, entries, strict=True):
        winner = fold_winners[w_idx]
        outcome = _score(objective, entry.metrics) if entry.ok else _failed_outcome(entry.error)
        per_winner_metrics[w_idx].append(entry.metrics)
        per_winner_outcomes[w_idx].append(outcome)
        row = TrialRow(
            trial_id=next(trial_counter),
            round=0,
            phase=f"final_cross_{f_idx}",
            fold_index=f_idx,
            params=winner.params,
            seed=template.seed,
            metrics=entry.metrics,
            score=outcome.score,
            accepted=outcome.accepted,
            reject_reason="" if outcome.accepted else _reject_reason(entry, outcome),
            wall_secs=entry.wall_secs,
        )
        trial_rows.append(row)
        if persist_writer is not None:
            persist_writer.emit_row(row)
    if persist_writer is not None:
        persist_writer.flush()
    outcomes: list[CrossValidationOutcome] = []
    for w_idx, winner in enumerate(fold_winners):
        agg_metrics = _aggregate_mean(per_winner_metrics[w_idx])
        fold_outcomes = per_winner_outcomes[w_idx]
        # Score the aggregate by averaging per-fold objective scores. We
        # deliberately do not re-evaluate the Rust objective on aggregate
        # metrics because the engine reports `n_trades` as u32 and a mean
        # of integer counts is generally fractional — rounding it would
        # discard signal. Each per-fold score saw integer-precise metrics
        # already, so the mean of those scores is the right aggregate.
        valid_scores = [o.score for o in fold_outcomes if o.score > float("-inf")]
        if valid_scores and len(valid_scores) == len(fold_outcomes):
            agg_score = sum(valid_scores) / len(valid_scores)
        else:
            agg_score = float("-inf")
        # Aggregate is accepted only if every fold's outcome was accepted
        # (any constraint violation in any fold rejects the candidate).
        agg_accepted = bool(fold_outcomes) and all(o.accepted for o in fold_outcomes)
        agg_violations: list[str] = []
        for f_idx, o in enumerate(fold_outcomes):
            if not o.accepted:
                agg_violations.extend(f"fold_{f_idx}:{v}" for v in o.violations)
        variance = (
            statistics.pvariance(valid_scores) if len(valid_scores) >= 2 else 0.0  # noqa: PLR2004
        )
        outcomes.append(
            CrossValidationOutcome(
                fold_index=winner.fold_index,
                params=winner.params,
                oos_metrics=per_winner_metrics[w_idx],
                aggregate_metrics=agg_metrics,
                aggregate_score=agg_score,
                aggregate_accepted=agg_accepted,
                aggregate_reject_reason=(
                    "" if agg_accepted else (",".join(agg_violations) or "below_gate")
                ),
                score_variance=variance,
            )
        )
    return outcomes


def _select_final(cross: Sequence[CrossValidationOutcome]) -> CrossValidationOutcome | None:
    accepted = [c for c in cross if c.aggregate_accepted]
    if not accepted:
        return None
    best_score = max(c.aggregate_score for c in accepted)
    contenders = [c for c in accepted if abs(c.aggregate_score - best_score) <= _TIE_EPSILON]
    if len(contenders) == 1:
        return contenders[0]
    # Tie-break: lower variance wins.
    return min(contenders, key=lambda c: c.score_variance)


# ---------------------------------------------------------------------------
# Persistence writer protocol
# ---------------------------------------------------------------------------


class _PersistWriter(Protocol):
    """Structural type implemented by :mod:`strategy_gpt.optimization_ledger`.

    Declared as a Protocol so the orchestrator can be unit-tested without
    exercising the parquet / sqlite paths and so concrete writers do not
    need to inherit a base class.
    """

    def start(  # noqa: PLR0913 — manifest fields are part of the wire shape.
        self,
        *,
        experiment: ExperimentSpec,
        objective: Mapping[str, Any],
        dataset_manifest: str,
        artifact_path: Path,
        opt_id: str,
        resolved_parallelism: int,
        seed: int,
        started_at: datetime,
        folds: Sequence[FoldRange],
    ) -> None: ...

    def emit_row(self, row: TrialRow) -> None: ...

    def flush(self) -> None: ...

    def finish(self, result: OptimizationResult) -> None: ...


__all__ = [
    "CrossValidationOutcome",
    "FoldWinner",
    "OptimizationResult",
    "TrialRow",
    "per_dim_resolutions",
    "run_optimization",
    "translate_space",
]
