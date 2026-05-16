"""Benchmark mode for the optimizer.

Procedure (see ``design.md §4`` of the ``optimize-command`` change):

1. Sample N candidates uniformly from ``optimize.space`` (N=3 default).
2. Run those N x F (folds) backtests as one packed batch with
   ``failure_mode: continue``.
3. Measure median wall time per run, standard deviation, and worker-pool
   spinup (first-result latency minus median).
4. Compute the planned run-count for the configured method.
5. Predict total wall time (``total_runs x median_per_run /
   resolved_parallelism + spinup``) and ledger footprint
   (``total_runs x ~200 bytes``).
6. Print a structured report; the caller (CLI) handles the proceed
   prompt and ``--yes`` skip.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .engine import Engine
from .experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from .experiment_spec import (
    DEKnobs,
    ExperimentSpec,
    OptimizeBlock,
    RecursiveGridKnobs,
    SobolKnobs,
)
from .experiment_spec import (
    FloatParam as SpecFloatParam,
)
from .experiment_spec import (
    IntParam as SpecIntParam,
)
from .folds import derive_folds
from .optimization_runner import (
    _build_run,
    _pack_batch,
    _submit_and_collect,
    per_dim_resolutions,
)
from .optimizer import _next_power_of_two, de_resolve_popsize
from .types import Bar

_LEDGER_BYTES_PER_ROW = 200
"""Rough compressed parquet row size from design.md §6."""

_BANDWIDTH = 0.2
"""±20% confidence band for the predicted-wall interval."""


@dataclass(frozen=True)
class BenchmarkReport:
    """Benchmark sample + extrapolated cost estimate."""

    sample_size: int
    median_per_run_secs: float
    stdev_per_run_secs: float
    spinup_secs: float
    parallelism: int
    method: str
    planned_total_runs: int
    predicted_wall_secs_low: float
    predicted_wall_secs_high: float
    predicted_ledger_bytes: int


def _sample_random_candidates(optim: OptimizeBlock, n: int) -> list[dict[str, Any]]:
    rng = random.Random(optim.seed)  # noqa: S311 — deterministic, non-cryptographic.
    out: list[dict[str, Any]] = []
    for _ in range(n):
        cand: dict[str, Any] = {}
        for name, param in optim.space.items():
            if isinstance(param, SpecChoiceParam):
                cand[name] = rng.choice(list(param.choices))
            elif isinstance(param, SpecIntParam):
                cand[name] = rng.randint(param.low, param.high)
            elif isinstance(param, SpecFloatParam):
                cand[name] = rng.uniform(param.low, param.high)
            else:  # pragma: no cover — pydantic union forbids other types.
                msg = f"benchmark: unsupported space entry for {name!r}: {type(param).__name__}"
                raise TypeError(msg)
        out.append(cand)
    return out


def planned_run_count(optim: OptimizeBlock, folds_count: int) -> int:  # noqa: PLR0912 — one branch per method+param kind.
    """Total backtest runs the configured method will commission."""
    method = optim.method
    if method == "recursive_grid":
        rg_knobs = optim.recursive_grid or RecursiveGridKnobs()
        dims = len(optim.space)
        per_dim_res = per_dim_resolutions(optim.space)
        runs_per_round = 1
        for name in optim.space:
            runs_per_round *= int(per_dim_res.get(name, rg_knobs.resolution))
        if dims == 0:
            runs_per_round = 0
        total_train = runs_per_round * rg_knobs.depth * folds_count
        return total_train + folds_count * folds_count
    if method == "grid":
        grid_knobs = optim.grid
        default_res = grid_knobs.resolution if grid_knobs and grid_knobs.resolution else 10
        size = 1
        for _name, param in optim.space.items():
            if isinstance(param, SpecChoiceParam):
                size *= len(param.choices)
            elif isinstance(param, SpecIntParam):
                if param.step is not None:
                    points = ((param.high - param.low) // param.step) + 1
                    size *= int(points)
                else:
                    size *= default_res
            elif isinstance(param, SpecFloatParam):
                if param.step is not None:
                    points = int((param.high - param.low) / param.step) + 1
                    size *= max(points, 1)
                else:
                    size *= default_res
        return size * folds_count + folds_count * folds_count
    if method == "random":
        n = optim.random.n_samples if optim.random is not None else 0
        return n * folds_count + folds_count * folds_count
    if method == "bayesian":
        n = optim.bayesian.n_iter if optim.bayesian is not None else 0
        return n * folds_count + folds_count * folds_count
    if method == "sobol":
        sobol_knobs = optim.sobol if optim.sobol is not None else SobolKnobs()
        n = _next_power_of_two(sobol_knobs.n_points)
        return n * folds_count + folds_count * folds_count
    if method == "differential_evolution":
        de_knobs = (
            optim.differential_evolution if optim.differential_evolution is not None else DEKnobs()
        )
        # Strip categoricals out of the dim count — DE rejects them at fold time.
        n_dims = sum(1 for p in optim.space.values() if not isinstance(p, SpecChoiceParam))
        pop = de_resolve_popsize(de_knobs.popsize, n_dims)
        return pop * de_knobs.n_generations * folds_count + folds_count * folds_count
    msg = f"planned_run_count: unsupported method {method!r}."
    raise ValueError(msg)


def run_benchmark(  # noqa: PLR0913
    *,
    experiment: ExperimentSpec,
    engine: Engine,
    artifact_path: Path,
    bars: list[Bar],
    dataset_manifest: str,
    sample_size: int = 3,
    poll_interval_secs: float = 0.05,
) -> BenchmarkReport:
    """Sample candidates, measure per-run wall, and predict total cost."""
    if experiment.optimize is None or experiment.folds is None:
        msg = "run_benchmark: experiment spec missing `optimize` or `folds` block."
        raise ValueError(msg)
    if sample_size < 1:
        msg = f"run_benchmark: sample_size must be >= 1, got {sample_size}."
        raise ValueError(msg)
    template = experiment.runs[0]
    folds = derive_folds(template.slice, experiment.folds)
    parallelism = experiment.resolved_parallelism()
    candidates = _sample_random_candidates(experiment.optimize, sample_size)
    runs = [_build_run(template, c, f.train) for c in candidates for f in folds]
    spec = _pack_batch(
        experiment=experiment,
        dataset_manifest=dataset_manifest,
        runs=runs,
        parallelism=parallelism,
    )
    start = time.monotonic()
    entries = _submit_and_collect(
        engine,
        artifact_path=artifact_path,
        bars=bars,
        spec=spec,
        dataset_manifest=dataset_manifest,
        poll_interval_secs=poll_interval_secs,
    )
    elapsed = time.monotonic() - start
    walls = [e.wall_secs for e in entries if e.ok]
    if not walls:
        median = elapsed
        stdev = 0.0
    else:
        median = statistics.median(walls)
        stdev = statistics.pstdev(walls) if len(walls) >= 2 else 0.0  # noqa: PLR2004
    # Spinup: total wall vs. ideal-parallel wall. The packed batch's
    # observed elapsed minus (runs / parallelism x median) is a rough
    # proxy for worker-pool startup overhead.
    ideal_parallel = (len(runs) * median) / max(parallelism, 1) if median > 0 else 0.0
    spinup = max(0.0, elapsed - ideal_parallel)
    total = planned_run_count(experiment.optimize, len(folds))
    parallel_runs = max(parallelism, 1)
    base = (total * median) / parallel_runs + spinup
    low = base * (1.0 - _BANDWIDTH)
    high = base * (1.0 + _BANDWIDTH)
    return BenchmarkReport(
        sample_size=sample_size,
        median_per_run_secs=median,
        stdev_per_run_secs=stdev,
        spinup_secs=spinup,
        parallelism=parallelism,
        method=experiment.optimize.method,
        planned_total_runs=total,
        predicted_wall_secs_low=low,
        predicted_wall_secs_high=high,
        predicted_ledger_bytes=total * _LEDGER_BYTES_PER_ROW,
    )


def format_report(report: BenchmarkReport) -> str:
    """Plain-text rendering for stdout."""
    bytes_mb = report.predicted_ledger_bytes / (1024 * 1024)
    return (
        f"benchmark sample: {report.sample_size}\n"
        f"  per-run median:    {report.median_per_run_secs:.3f}s\n"
        f"  per-run stdev:     {report.stdev_per_run_secs:.3f}s\n"
        f"  spinup overhead:   {report.spinup_secs:.3f}s\n"
        f"  parallelism:       {report.parallelism}\n"
        f"method:              {report.method}\n"
        f"planned total runs:  {report.planned_total_runs}\n"
        f"predicted wall:      {report.predicted_wall_secs_low:.1f}s "
        f"-{report.predicted_wall_secs_high:.1f}s (±{int(_BANDWIDTH * 100)}%)\n"
        f"predicted ledger:    {bytes_mb:.1f} MiB\n"
    )


def report_json(report: BenchmarkReport) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=True)


__all__ = [
    "BenchmarkReport",
    "format_report",
    "planned_run_count",
    "report_json",
    "run_benchmark",
]
