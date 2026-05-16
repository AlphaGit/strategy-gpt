"""Parameter optimizer driver + grid / random searchers.

The optimizer is algorithmically agnostic: it enumerates candidate parameter
sets via a :class:`Searcher`, evaluates each with a caller-supplied
``evaluate`` callable that returns the candidate's metrics, scores those
metrics with a caller-supplied ``score`` callable that returns an
:class:`~strategy_gpt.types.EvaluationOutcome`, and optionally gates trials
on `oos_min_score`. Walk-forward fold orchestration and engine submission
live above this layer — this module owns the search algorithms only.

Built-in searchers
------------------
- :class:`GridSearcher` — cartesian product over a discrete grid; finite,
  deterministic in iteration order.
- :class:`RandomSearcher` — uniform sampling from a space of discrete
  choices and/or numeric ranges, seeded so two optimizer runs with the
  same seed produce byte-identical candidate sequences.

Bayesian search via Tree-structured Parzen Estimator
----------------------------------------------------
- :class:`TPESearcher` — sequential SMBO. Unlike Grid/Random, TPE needs to
  *observe* trial outcomes to inform later proposals, so it exposes a
  :meth:`TPESearcher.search` method that owns the evaluate / score / observe
  loop instead of plugging into the stateless :func:`optimize` driver.
- :class:`RecursiveGridSearcher` — round-wise uniform grid that shrinks
  the search box to the union of the top-`k` cells each round, stopping
  on `depth` or a per-dimension plateau. Like TPE it owns its own
  :meth:`RecursiveGridSearcher.search` loop; round-wise candidate sets
  are dispatched to a caller-supplied ``evaluate_batch`` so the
  orchestrator can pack each round as a single engine batch.

Reference for the algorithm: Bergstra et al. (2011), "Algorithms for
Hyper-Parameter Optimization"; Optuna's
``optuna.samplers._tpe.sampler``. The implementation here is in-house and
deliberately compact — it covers what the research loop needs (Gaussian
KDE for numeric params, categorical Parzen with Laplace smoothing for
choices) and skips multivariate / multi-objective extensions.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .types import EvaluationOutcome

ParamSet = dict[str, Any]
MetricsDict = dict[str, float]


class Searcher(Protocol):
    """Yields candidate parameter sets to evaluate."""

    def candidates(self) -> Iterator[ParamSet]:
        """Yield successive candidates. Should be deterministic for replay."""
        ...


@dataclass(frozen=True)
class Trial:
    """Result of evaluating a single candidate."""

    params: ParamSet
    metrics: MetricsDict
    outcome: EvaluationOutcome
    accepted: bool
    """`outcome.accepted` ANDed with the optional `oos_min_score` gate."""


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSearcher:
    """Cartesian product over a discrete grid.

    Example
    -------
    >>> GridSearcher({"lookback": [10, 20], "threshold": [0.5, 1.0]}).count()
    4
    """

    grid: Mapping[str, Sequence[Any]]

    def candidates(self) -> Iterator[ParamSet]:
        keys = list(self.grid.keys())
        values = [list(self.grid[k]) for k in keys]
        for combo in itertools.product(*values):
            yield dict(zip(keys, combo, strict=True))

    def count(self) -> int:
        n = 1
        for values in self.grid.values():
            n *= len(values)
        return n


# ---------------------------------------------------------------------------
# Random search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuousParam:
    """Uniform-sampled numeric range [`low`, `high`)."""

    low: float
    high: float
    log: bool = False
    """If True, sample uniformly in log-space (useful for learning-rate-like
    params spanning multiple orders of magnitude)."""


@dataclass(frozen=True)
class IntParam:
    """Uniform-sampled integer range [`low`, `high`] (inclusive)."""

    low: int
    high: int


@dataclass(frozen=True)
class ChoiceParam:
    """Uniform-sampled categorical choice."""

    choices: Sequence[Any]


RandomParam = ContinuousParam | IntParam | ChoiceParam


@dataclass(frozen=True)
class RandomSearcher:
    """Uniformly sample from `space` for `n_iter` iterations, seeded."""

    space: Mapping[str, RandomParam]
    n_iter: int
    seed: int

    def candidates(self) -> Iterator[ParamSet]:
        rng = random.Random(self.seed)  # noqa: S311 — non-cryptographic by design
        for _ in range(self.n_iter):
            yield {name: _sample(rng, param) for name, param in self.space.items()}


def _sample(rng: random.Random, param: RandomParam) -> Any:  # noqa: ANN401 — heterogeneous sample types are the point.
    if isinstance(param, ContinuousParam):
        if param.log:
            lo, hi = math.log(param.low), math.log(param.high)
            return math.exp(rng.uniform(lo, hi))
        return rng.uniform(param.low, param.high)
    if isinstance(param, IntParam):
        return rng.randint(param.low, param.high)
    return rng.choice(list(param.choices))


# ---------------------------------------------------------------------------
# Sobol quasi-random search
# ---------------------------------------------------------------------------


def _next_power_of_two(n: int) -> int:
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def _project_unit(u: float, param: RandomParam) -> Any:  # noqa: ANN401
    """Map a unit-interval value to a parameter sample."""
    if isinstance(param, ContinuousParam):
        if param.log:
            lo, hi = math.log(param.low), math.log(param.high)
            return math.exp(lo + u * (hi - lo))
        return param.low + u * (param.high - param.low)
    if isinstance(param, IntParam):
        # Inclusive [low, high]: uniformly bucket the unit interval into
        # (high - low + 1) bins.
        span = param.high - param.low + 1
        idx = min(int(u * span), span - 1)
        return param.low + idx
    choices = list(param.choices)
    idx = min(int(u * len(choices)), len(choices) - 1)
    return choices[idx]


@dataclass(frozen=True)
class SobolSearcher:
    """Owen-scrambled Sobol quasi-random sequence over ``space``.

    Yields exactly ``n_points`` candidates (rounded up to the next power
    of two with a warning when callers request a non-power-of-2). The
    sequence is deterministic given ``owen_seed`` when ``scramble=True``
    and deterministic by construction when ``scramble=False``.

    Reference: Owen 1995, Owen-scrambling of Sobol sequences;
    `scipy.stats.qmc.Sobol`.
    """

    space: Mapping[str, RandomParam]
    n_points: int
    scramble: bool = True
    owen_seed: int = 0

    def candidates(self) -> Iterator[ParamSet]:
        # Imported lazily so a stripped-down env without scipy can still
        # load this module — only callers requesting Sobol pay the import.
        from scipy.stats import qmc  # noqa: PLC0415 — optional dep, deferred import.

        keys = list(self.space.keys())
        if not keys:
            return
        d = len(keys)
        target = _next_power_of_two(self.n_points)
        if target != self.n_points:
            import warnings as _w  # noqa: PLC0415 — lazy import; warn only on the slow path.

            _w.warn(
                f"SobolSearcher: n_points={self.n_points} rounded up to {target} "
                "(power of two required for balanced Sobol sequences).",
                UserWarning,
                stacklevel=2,
            )
        engine = qmc.Sobol(
            d=d,
            scramble=self.scramble,
            seed=self.owen_seed if self.scramble else None,
        )
        unit = engine.random(n=target)
        for row in unit:
            yield {k: _project_unit(float(row[i]), self.space[k]) for i, k in enumerate(keys)}

    def count(self) -> int:
        return _next_power_of_two(self.n_points)


# ---------------------------------------------------------------------------
# Differential Evolution helpers
# ---------------------------------------------------------------------------


def de_bounds_and_integrality(
    space: Mapping[str, RandomParam],
) -> tuple[list[str], list[tuple[float, float]], list[bool]]:
    """Project an optimizer search space to scipy DE's (bounds, integrality).

    Categoricals (:class:`ChoiceParam`) are not supported; declare them as
    integers if you need DE to sweep them. ``log``-scaled continuous params
    are projected in linear space — log-scaled DE is not commonly useful
    for the bounded ranges this platform produces.
    """
    keys: list[str] = []
    bounds: list[tuple[float, float]] = []
    integrality: list[bool] = []
    for name, p in space.items():
        if isinstance(p, ChoiceParam):
            msg = (
                f"DE does not support ChoiceParam ({name!r}); declare it as "
                "an IntParam with a numeric encoding instead."
            )
            raise TypeError(msg)
        if isinstance(p, IntParam):
            keys.append(name)
            bounds.append((float(p.low), float(p.high)))
            integrality.append(True)
        elif isinstance(p, ContinuousParam):
            keys.append(name)
            bounds.append((float(p.low), float(p.high)))
            integrality.append(False)
        else:  # pragma: no cover — RandomParam union is exhaustive.
            msg = f"unsupported space entry: {type(p).__name__}"
            raise TypeError(msg)
    return keys, bounds, integrality


def de_project_individual(
    individual: Sequence[float],
    keys: Sequence[str],
    integrality: Sequence[bool],
) -> ParamSet:
    """Map a DE individual (vector of floats) back to a named param set."""
    out: ParamSet = {}
    for i, k in enumerate(keys):
        v = float(individual[i])
        out[k] = round(v) if integrality[i] else v
    return out


def de_resolve_popsize(popsize: int | str, n_dims: int) -> int:
    """Resolve ``popsize: auto`` → ``15 * D`` per Storn & Price defaults."""
    if isinstance(popsize, int):
        return max(popsize, 5)
    if popsize == "auto":
        return max(15 * n_dims, 5)
    msg = f"de_resolve_popsize: unexpected popsize={popsize!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# CMA-ES helpers
# ---------------------------------------------------------------------------


def cma_resolve_popsize(popsize: int | str, n_dims: int) -> int:
    """Resolve ``popsize: auto`` -> ``4 + floor(3 * ln(D))`` per Hansen 2016."""
    if isinstance(popsize, int):
        return max(popsize, 4)
    if popsize == "auto":
        return 4 + int(3 * math.log(max(n_dims, 1)))
    msg = f"cma_resolve_popsize: unexpected popsize={popsize!r}"
    raise ValueError(msg)


def cma_unit_to_params(
    unit: Sequence[float],
    keys: Sequence[str],
    bounds: Sequence[tuple[float, float]],
    integrality: Sequence[bool],
    bounds_mode: str,
) -> ParamSet:
    """Project a unit-cube vector to a named param set.

    Under ``bounds: clip`` the value is clamped to ``[low, high]``; the
    caller is responsible for the redraw policy under ``bounds: reject``.
    """
    out: ParamSet = {}
    for i, k in enumerate(keys):
        u = float(unit[i])
        if bounds_mode == "clip":
            u = min(1.0, max(0.0, u))
        lo, hi = bounds[i]
        raw = lo + u * (hi - lo)
        out[k] = round(raw) if integrality[i] else raw
    return out


def _unit_out_of_bounds(unit: Sequence[float]) -> bool:
    return any(u < 0.0 or u > 1.0 for u in unit)


def cma_dedup_rate(params_list: Sequence[Mapping[str, Any]]) -> float:
    """Fraction of duplicates within a generation.

    Used to detect integer-collapse pathologies — when too many
    candidates round to the same point, CMA-ES is stuck and the
    orchestrator should warn / inflate sigma.
    """
    if not params_list:
        return 0.0
    keys = sorted(params_list[0].keys())
    fingerprints = [tuple(p[k] for k in keys) for p in params_list]
    unique = len(set(fingerprints))
    return 1.0 - unique / len(fingerprints)


# ---------------------------------------------------------------------------
# Latin Hypercube + Hooke-Jeeves polish helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LhsSearcher:
    """Latin Hypercube sampler over ``space``.

    Wraps :class:`scipy.stats.qmc.LatinHypercube` and projects unit-cube
    samples through the same :func:`_project_unit` map :class:`SobolSearcher`
    uses, so int / choice / log-continuous dims are honored identically.
    Deterministic given ``seed``.
    """

    space: Mapping[str, RandomParam]
    n_points: int
    seed: int = 0

    def candidates(self) -> Iterator[ParamSet]:
        from scipy.stats import qmc  # noqa: PLC0415 — optional dep.

        keys = list(self.space.keys())
        if not keys:
            return
        d = len(keys)
        engine = qmc.LatinHypercube(d=d, seed=self.seed)
        unit = engine.random(n=self.n_points)
        for row in unit:
            yield {k: _project_unit(float(row[i]), self.space[k]) for i, k in enumerate(keys)}


def hooke_jeeves_propose(base_unit: Sequence[float], step: Sequence[float]) -> list[list[float]]:
    """Propose ``2D`` axis-aligned probes ``base ± step`` per dimension.

    Returns the probe list in fixed order so the orchestrator's packed
    batch is deterministic. Probes are returned in unit-cube space; the
    caller clamps to [0, 1] and projects to the named param space.
    """
    d = len(base_unit)
    probes: list[list[float]] = []
    for i in range(d):
        plus = list(base_unit)
        minus = list(base_unit)
        plus[i] = min(1.0, plus[i] + step[i])
        minus[i] = max(0.0, minus[i] - step[i])
        probes.append(plus)
        probes.append(minus)
    return probes


def de_sobol_init(
    space: Mapping[str, RandomParam],
    keys: Sequence[str],
    popsize: int,
    seed: int,
) -> Any:  # noqa: ANN401 — returns np.ndarray; ndarray import deferred.
    """Build a Sobol-seeded init array (``popsize * D``) for scipy DE.

    Mirrors :class:`SobolSearcher` so the first generation matches a
    standalone Sobol run with the same seed and ``n=popsize``.
    """
    import numpy as np  # noqa: PLC0415 — optional dep, deferred import.

    points = list(
        SobolSearcher(
            space={k: space[k] for k in keys},
            n_points=popsize,
            scramble=True,
            owen_seed=seed,
        ).candidates()
    )
    # Truncate to exact popsize (Sobol may round up to a power of two).
    points = points[:popsize]
    if len(points) < popsize:
        # Pad with random points to fill — extremely unlikely path.
        rng = random.Random(seed)  # noqa: S311 — non-cryptographic by design.
        while len(points) < popsize:
            points.append({k: _sample(rng, space[k]) for k in keys})
    return np.array(
        [[float(p[k]) for k in keys] for p in points],
        dtype=float,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


EvaluateFn = Callable[[ParamSet], MetricsDict]
"""Caller-supplied: run the candidate (e.g. submit to engine, aggregate folds)
and return its metrics dict. The optimizer treats this as a black box; in
production it dispatches a fold-based `BatchSpec` to the engine."""

ScoreFn = Callable[[MetricsDict], EvaluationOutcome]
"""Caller-supplied: produce an `EvaluationOutcome` from metrics. In
production this is the `objectives.evaluate_spec` wrapper applied to the
strategy's `ObjectiveSpec`."""


@dataclass(frozen=True)
class OptimizerResult:
    """Outcome of a full optimization run."""

    trials: list[Trial]
    best: Trial | None
    """The best `accepted` trial under the objective's tradeoff. `None` if
    no trial was accepted (every candidate either violated constraints or
    fell below the `oos_min_score` gate)."""

    rejected_count: int = field(default=0)


def optimize(
    searcher: Searcher,
    evaluate: EvaluateFn,
    score: ScoreFn,
    *,
    oos_min_score: float | None = None,
) -> OptimizerResult:
    """Run an optimization pass: enumerate candidates, evaluate, score, gate.

    Trials are produced in candidate-submission order. The best trial is
    selected by maximum `outcome.score` across accepted trials.
    """
    trials: list[Trial] = []
    rejected = 0
    for params in searcher.candidates():
        metrics = evaluate(params)
        outcome = score(metrics)
        gate_pass = oos_min_score is None or outcome.score >= oos_min_score
        accepted = outcome.accepted and gate_pass
        if not accepted:
            rejected += 1
        trials.append(Trial(params=params, metrics=metrics, outcome=outcome, accepted=accepted))
    best = max(
        (t for t in trials if t.accepted),
        key=lambda t: t.outcome.score,
        default=None,
    )
    return OptimizerResult(trials=trials, best=best, rejected_count=rejected)


# ---------------------------------------------------------------------------
# Tree-structured Parzen Estimator (TPE)
# ---------------------------------------------------------------------------


_EPS = 1e-12
_LAPLACE_PRIOR = 1.0
"""Additive smoothing for categorical TPE. Stops the `l(x)/g(x)` ratio
from blowing up when `g(x)` is zero on a category that the bad-set has
never picked."""


@dataclass(frozen=True)
class TPESearcher:
    """Sequential model-based optimizer using Tree-structured Parzen Estimators.

    The search loop:

    1. Sample the first ``n_startup_trials`` candidates uniformly from
       ``space`` (same distribution as :class:`RandomSearcher`).
    2. For each subsequent step, split observations into a "good" set
       (top ``gamma`` fraction by score) and a "bad" set (the rest), fit
       a Parzen estimator per parameter for each set, draw
       ``n_candidates_per_step`` candidates from the good distribution,
       and pick the one maximizing :math:`l(x)/g(x)`.

    Determinism: a fixed ``seed`` yields a reproducible trial sequence for
    a given ``evaluate`` / ``score`` pair.

    Unlike :class:`GridSearcher` / :class:`RandomSearcher`, TPE drives the
    optimization loop directly through :meth:`search` — it cannot plug into
    :func:`optimize`, since that driver is feedback-free.
    """

    space: Mapping[str, RandomParam]
    n_iter: int
    seed: int
    n_startup_trials: int = 10
    gamma: float = 0.25
    n_candidates_per_step: int = 24

    def search(
        self,
        evaluate: EvaluateFn,
        score: ScoreFn,
        *,
        oos_min_score: float | None = None,
    ) -> OptimizerResult:
        """Run TPE for ``n_iter`` iterations and return an :class:`OptimizerResult`."""
        if self.n_iter <= 0:
            return OptimizerResult(trials=[], best=None, rejected_count=0)
        if not (0.0 < self.gamma < 1.0):
            msg = f"gamma must be in (0, 1); got {self.gamma}"
            raise ValueError(msg)

        rng = random.Random(self.seed)  # noqa: S311 — non-cryptographic by design
        trials: list[Trial] = []
        rejected = 0
        # Each entry is (params, score). Higher score = better.
        history: list[tuple[ParamSet, float]] = []

        for i in range(self.n_iter):
            params = (
                self._random_sample(rng)
                if i < self.n_startup_trials
                else self._tpe_sample(rng, history)
            )
            metrics = evaluate(params)
            outcome = score(metrics)
            gate_pass = oos_min_score is None or outcome.score >= oos_min_score
            accepted = outcome.accepted and gate_pass
            if not accepted:
                rejected += 1
            trials.append(Trial(params=params, metrics=metrics, outcome=outcome, accepted=accepted))
            history.append((params, outcome.score))

        best = max(
            (t for t in trials if t.accepted),
            key=lambda t: t.outcome.score,
            default=None,
        )
        return OptimizerResult(trials=trials, best=best, rejected_count=rejected)

    def _random_sample(self, rng: random.Random) -> ParamSet:
        return {name: _sample(rng, param) for name, param in self.space.items()}

    def _tpe_sample(
        self,
        rng: random.Random,
        history: Sequence[tuple[ParamSet, float]],
    ) -> ParamSet:
        # Split by gamma quantile: top fraction = "good", rest = "bad".
        sorted_hist = sorted(history, key=lambda h: h[1], reverse=True)
        split = max(1, round(self.gamma * len(sorted_hist)))
        split = min(split, len(sorted_hist) - 1) if len(sorted_hist) > 1 else 1
        good = [h[0] for h in sorted_hist[:split]]
        bad = [h[0] for h in sorted_hist[split:]]
        # If bad is empty (very small history), fall back to a random draw —
        # `l(x) / g(x)` is undefined in that case.
        if not bad:
            return self._random_sample(rng)

        best_params: ParamSet | None = None
        best_ratio = -math.inf
        for _ in range(self.n_candidates_per_step):
            candidate = self._sample_from_observations(rng, good)
            log_l = self._log_density(candidate, good)
            log_g = self._log_density(candidate, bad)
            ratio = log_l - log_g
            if ratio > best_ratio:
                best_ratio = ratio
                best_params = candidate
        # `best_params` is guaranteed non-None since the loop runs at least once
        # (n_candidates_per_step >= 1 by construction).
        assert best_params is not None  # noqa: S101 — invariant, not user input
        return best_params

    def _sample_from_observations(
        self,
        rng: random.Random,
        observations: Sequence[ParamSet],
    ) -> ParamSet:
        """Draw one candidate by sampling each param independently from
        the Parzen mixture fit to `observations`.
        """
        return {
            name: _sample_parzen(rng, param, [obs[name] for obs in observations])
            for name, param in self.space.items()
        }

    def _log_density(
        self,
        candidate: ParamSet,
        observations: Sequence[ParamSet],
    ) -> float:
        """Sum of per-parameter log-densities of the Parzen mixture fit to
        `observations`, evaluated at `candidate`. Assumes parameter independence.
        """
        if not observations:
            # Uniform fallback — every candidate equally likely.
            return 0.0
        total = 0.0
        for name, param in self.space.items():
            obs_values = [obs[name] for obs in observations]
            density = _parzen_density(candidate[name], param, obs_values)
            total += math.log(max(density, _EPS))
        return total


def _sample_parzen(
    rng: random.Random,
    param: RandomParam,
    observations: Sequence[Any],
) -> Any:  # noqa: ANN401
    """Draw one sample from the Parzen mixture fit to `observations`."""
    if isinstance(param, ChoiceParam):
        choices = list(param.choices)
        counts = dict.fromkeys(choices, _LAPLACE_PRIOR)
        for o in observations:
            if o in counts:
                counts[o] += 1.0
        total = sum(counts.values())
        weights = [counts[c] / total for c in choices]
        return rng.choices(choices, weights=weights, k=1)[0]
    if isinstance(param, IntParam):
        # Continuous Parzen in the integer's natural scale, then round/clip.
        cont = _sample_continuous_parzen(
            rng,
            observations=[float(o) for o in observations],
            low=float(param.low),
            high=float(param.high),
            log_scale=False,
        )
        return int(max(param.low, min(param.high, round(cont))))
    # ContinuousParam
    low = math.log(param.low) if param.log else param.low
    high = math.log(param.high) if param.log else param.high
    obs_transformed = [math.log(o) if param.log else o for o in observations]
    cont = _sample_continuous_parzen(
        rng,
        observations=obs_transformed,
        low=low,
        high=high,
        log_scale=False,
    )
    return math.exp(cont) if param.log else cont


def _sample_continuous_parzen(
    rng: random.Random,
    *,
    observations: Sequence[float],
    low: float,
    high: float,
    log_scale: bool,
) -> float:
    """Continuous Parzen: pick an observation, sample N(obs, bandwidth),
    clip to [low, high].
    """
    if log_scale:
        msg = "log_scale=True is handled by the caller via pre-transform"
        raise ValueError(msg)
    if not observations:
        return rng.uniform(low, high)
    centre = rng.choice(list(observations))
    bandwidth = _bandwidth(observations, low, high)
    sample = rng.gauss(centre, bandwidth)
    if sample < low:
        return low
    if sample > high:
        return high
    return sample


def _parzen_density(
    value: Any,  # noqa: ANN401 — heterogeneous sample types.
    param: RandomParam,
    observations: Sequence[Any],
) -> float:
    """Density of the Parzen mixture (fit to `observations`) at `value`."""
    if isinstance(param, ChoiceParam):
        choices = list(param.choices)
        counts = dict.fromkeys(choices, _LAPLACE_PRIOR)
        for o in observations:
            if o in counts:
                counts[o] += 1.0
        total = sum(counts.values())
        return counts.get(value, _LAPLACE_PRIOR) / total
    if isinstance(param, IntParam):
        return _continuous_parzen_density(
            float(value),
            obs=[float(o) for o in observations],
            low=float(param.low),
            high=float(param.high),
        )
    # ContinuousParam: evaluate density on the transformed (log if applicable)
    # scale so the bandwidth matches the sampling space.
    val_t = math.log(value) if param.log else float(value)
    obs_t = [math.log(o) if param.log else float(o) for o in observations]
    low_t = math.log(param.low) if param.log else param.low
    high_t = math.log(param.high) if param.log else param.high
    return _continuous_parzen_density(val_t, obs=obs_t, low=low_t, high=high_t)


def _continuous_parzen_density(
    value: float,
    *,
    obs: Sequence[float],
    low: float,
    high: float,
) -> float:
    """Mixture-of-Gaussians density at `value`, mean per observation,
    bandwidth shared via Silverman-like rule.
    """
    if not obs:
        return 1.0 / max(high - low, _EPS)
    bandwidth = _bandwidth(obs, low, high)
    inv = 1.0 / (math.sqrt(2.0 * math.pi) * bandwidth)
    acc = 0.0
    for o in obs:
        z = (value - o) / bandwidth
        acc += inv * math.exp(-0.5 * z * z)
    return acc / len(obs)


def _bandwidth(obs: Sequence[float], low: float, high: float) -> float:
    """Adaptive bandwidth for the Parzen mixture.

    A practical compromise of Silverman's rule and Optuna's heuristic: take
    the larger of the observation standard deviation and a small fraction
    of the range, scaled by ``n^(-1/5)``. Guarantees a positive, finite
    value even when all observations collide.
    """
    n = len(obs)
    if n == 0:
        return max(high - low, _EPS) / 4.0
    mean = sum(obs) / n
    var = sum((o - mean) ** 2 for o in obs) / n if n > 0 else 0.0
    sigma = math.sqrt(var)
    span = max(high - low, _EPS)
    floor = span / max(n, 1)
    h = max(sigma, floor) * (n ** (-1.0 / 5.0))
    return float(max(h, _EPS))


# ---------------------------------------------------------------------------
# Recursive grid
# ---------------------------------------------------------------------------


EvaluateBatchFn = Callable[[Sequence[ParamSet]], Sequence[MetricsDict]]
"""Caller-supplied: evaluate a whole round's candidates in one shot and
return a parallel list of metrics dicts. Recursive grid hands the
orchestrator a round-shaped batch so the engine can dispatch it as a
single packed :class:`BatchSpec` with ``failure_mode: continue``."""


@dataclass(frozen=True)
class RecursiveGridSearcher:
    """Round-wise grid that shrinks toward the best-scoring cells.

    Algorithm (see ``design.md §2`` of the ``optimize-command`` change):

    1. Evaluate a uniform ``resolution^D`` grid within the current
       per-dimension box.
    2. Pick the top ``top_k`` candidates by score.
    3. Shrink each numeric dim's box to the bounding hull of the top
       points (plus a half-step margin), clipped to the current box.
    4. Stop when ``depth`` rounds have elapsed OR every numeric
       dimension's current width is below ``plateau_epsilon x
       original_range`` (AND over dims; choice dims and frozen integer
       dims are ignored in the convergence check).

    Per-dim resolution overrides are passed as
    :attr:`per_dim_resolution` so the caller can wire them from the
    experiment-spec's ``space.<param>.resolution``.

    Integer dims sample integer points only and freeze when the current
    cell collapses to a single integer (width < 1). Choice dims
    enumerate the full choice list at every round and never narrow.
    """

    space: Mapping[str, RandomParam]
    resolution: int = 10
    top_k: int = 1
    depth: int = 5
    plateau_epsilon: float = 1e-4
    seed: int = 0
    per_dim_resolution: Mapping[str, int] = field(default_factory=dict)

    def search(
        self,
        evaluate_batch: EvaluateBatchFn,
        score: ScoreFn,
        *,
        oos_min_score: float | None = None,
    ) -> OptimizerResult:
        """Run the recursive-grid loop and return all trials + the best."""
        self._validate()
        rng = random.Random(self.seed)  # noqa: S311 — non-cryptographic by design.
        box = _initial_box(self.space)
        original_widths = _original_widths(self.space)
        trials: list[Trial] = []
        rejected = 0

        for _round in range(self.depth):
            cells = list(self._round_candidates(box))
            metrics_list = list(evaluate_batch(cells))
            if len(metrics_list) != len(cells):
                msg = (
                    f"recursive_grid: evaluate_batch returned {len(metrics_list)} metrics "
                    f"for {len(cells)} candidates."
                )
                raise ValueError(msg)
            round_trials: list[Trial] = []
            for params, metrics in zip(cells, metrics_list, strict=True):
                outcome = score(metrics)
                gate_pass = oos_min_score is None or outcome.score >= oos_min_score
                accepted = outcome.accepted and gate_pass
                if not accepted:
                    rejected += 1
                trial = Trial(
                    params=params,
                    metrics=metrics,
                    outcome=outcome,
                    accepted=accepted,
                )
                round_trials.append(trial)
                trials.append(trial)
            top = self._select_top(round_trials, rng)
            new_box = _shrink_box(
                top_params=[t.params for t in top],
                current_box=box,
                space=self.space,
                resolution=self.resolution,
                per_dim_resolution=self.per_dim_resolution,
            )
            if _all_converged(new_box, original_widths, self.plateau_epsilon, self.space):
                break
            box = new_box

        best = max(
            (t for t in trials if t.accepted),
            key=lambda t: t.outcome.score,
            default=None,
        )
        return OptimizerResult(trials=trials, best=best, rejected_count=rejected)

    def _validate(self) -> None:
        if self.resolution < 2:  # noqa: PLR2004 — schema enforces >= 2.
            msg = f"recursive_grid: resolution must be >= 2, got {self.resolution}."
            raise ValueError(msg)
        if self.top_k < 1:
            msg = f"recursive_grid: top_k must be >= 1, got {self.top_k}."
            raise ValueError(msg)
        if self.depth < 1:
            msg = f"recursive_grid: depth must be >= 1, got {self.depth}."
            raise ValueError(msg)
        if self.plateau_epsilon <= 0:
            msg = f"recursive_grid: plateau_epsilon must be > 0, got {self.plateau_epsilon}."
            raise ValueError(msg)
        for name, override in self.per_dim_resolution.items():
            if name not in self.space:
                msg = (
                    f"recursive_grid: per_dim_resolution references unknown param "
                    f"{name!r}; space keys are {sorted(self.space)}."
                )
                raise ValueError(msg)
            if override < 2:  # noqa: PLR2004 — schema enforces >= 2.
                msg = f"recursive_grid: per_dim_resolution[{name!r}] must be >= 2, got {override}."
                raise ValueError(msg)

    def _round_candidates(self, box: Mapping[str, Any]) -> Iterator[ParamSet]:
        keys = list(self.space.keys())
        per_dim_points = [
            _dim_grid_points(
                self.space[name],
                box[name],
                self._resolution_for(name),
            )
            for name in keys
        ]
        for combo in itertools.product(*per_dim_points):
            yield dict(zip(keys, combo, strict=True))

    def _resolution_for(self, name: str) -> int:
        return int(self.per_dim_resolution.get(name, self.resolution))

    def _select_top(self, round_trials: Sequence[Trial], rng: random.Random) -> list[Trial]:
        # Deterministic tie-break: shuffle by a seeded RNG before sorting so
        # the sort order is fixed but ties don't bias the lowest-index
        # candidate, matching the determinism scenario in the spec.
        order = list(range(len(round_trials)))
        rng.shuffle(order)
        indexed = [round_trials[i] for i in order]
        indexed.sort(key=lambda t: t.outcome.score, reverse=True)
        return indexed[: self.top_k]


# Internal helpers shared with the recursive-grid algorithm.


def _initial_box(space: Mapping[str, RandomParam]) -> dict[str, Any]:
    box: dict[str, Any] = {}
    for name, param in space.items():
        if isinstance(param, ChoiceParam):
            box[name] = tuple(param.choices)
        elif isinstance(param, IntParam):
            box[name] = (float(param.low), float(param.high))
        else:  # ContinuousParam
            box[name] = (float(param.low), float(param.high))
    return box


def _original_widths(space: Mapping[str, RandomParam]) -> dict[str, float]:
    widths: dict[str, float] = {}
    for name, param in space.items():
        if isinstance(param, ChoiceParam):
            continue
        if isinstance(param, IntParam):
            widths[name] = float(param.high - param.low)
        else:
            widths[name] = float(param.high - param.low)
    return widths


def _dim_grid_points(
    param: RandomParam,
    current: Any,  # noqa: ANN401 — tuple for numeric, sequence for choice.
    resolution: int,
) -> list[Any]:
    if isinstance(param, ChoiceParam):
        return list(current)
    low, high = current
    if isinstance(param, IntParam):
        # Cell collapsed to a single int -> frozen dim.
        if high - low < 1.0:
            return [round((low + high) / 2.0)]
        pts = [round(low + (high - low) * i / (resolution - 1)) for i in range(resolution)]
        # Dedup while preserving order.
        seen: set[int] = set()
        unique: list[int] = []
        for p in pts:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique
    if high - low <= 0:
        return [float(low)]
    return [low + (high - low) * i / (resolution - 1) for i in range(resolution)]


def _shrink_box(
    *,
    top_params: Sequence[ParamSet],
    current_box: Mapping[str, Any],
    space: Mapping[str, RandomParam],
    resolution: int,
    per_dim_resolution: Mapping[str, int],
) -> dict[str, Any]:
    new_box: dict[str, Any] = {}
    for name, param in space.items():
        if isinstance(param, ChoiceParam):
            new_box[name] = current_box[name]  # never narrows
            continue
        low_c, high_c = current_box[name]
        res = int(per_dim_resolution.get(name, resolution))
        centres = [t[name] for t in top_params]
        if not centres:
            new_box[name] = (low_c, high_c)
            continue
        half_step = (high_c - low_c) / (res - 1) / 2.0 if res > 1 else 0.0
        lo = max(low_c, min(centres) - half_step)
        hi = min(high_c, max(centres) + half_step)
        if isinstance(param, IntParam) and hi - lo < 1.0:
            # Freeze: collapse to a single integer cell.
            centre = round((lo + hi) / 2.0)
            lo = float(centre)
            hi = float(centre)
        new_box[name] = (lo, hi)
    return new_box


class RecursiveGridDriver:
    """Stateful per-round driver for :class:`RecursiveGridSearcher`.

    Lets a caller (the optimization runner) own dispatch of each round's
    candidates as a packed engine batch. Usage::

        driver = RecursiveGridDriver(searcher)
        while not driver.done:
            cands = driver.candidates()
            outcomes = batch_evaluate(cands)
            trials = [Trial(p, m, score(m), True) for p, m in zip(cands, outcomes, strict=True)]
            driver.observe(trials)
    """

    def __init__(self, searcher: RecursiveGridSearcher, *, salt: int = 0) -> None:
        searcher._validate()
        self._s = searcher
        self.box: dict[str, Any] = _initial_box(searcher.space)
        self._original_widths = _original_widths(searcher.space)
        self._rng = random.Random(searcher.seed + salt)  # noqa: S311 — non-crypto.
        self.round_index: int = 0
        self.done: bool = False

    def candidates(self) -> list[ParamSet]:
        if self.done:
            return []
        keys = list(self._s.space.keys())
        per_dim = [
            _dim_grid_points(
                self._s.space[name],
                self.box[name],
                int(self._s.per_dim_resolution.get(name, self._s.resolution)),
            )
            for name in keys
        ]
        return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*per_dim)]

    def observe(self, trials: Sequence[Trial]) -> None:
        if self.done:
            return
        if not trials:
            self.done = True
            return
        order = list(range(len(trials)))
        self._rng.shuffle(order)
        ranked = sorted(
            [trials[i] for i in order],
            key=lambda t: t.outcome.score,
            reverse=True,
        )
        top = ranked[: self._s.top_k]
        new_box = _shrink_box(
            top_params=[t.params for t in top],
            current_box=self.box,
            space=self._s.space,
            resolution=self._s.resolution,
            per_dim_resolution=self._s.per_dim_resolution,
        )
        self.round_index += 1
        if self.round_index >= self._s.depth or _all_converged(
            new_box, self._original_widths, self._s.plateau_epsilon, self._s.space
        ):
            self.done = True
        self.box = new_box


def _all_converged(
    box: Mapping[str, Any],
    original_widths: Mapping[str, float],
    epsilon: float,
    space: Mapping[str, RandomParam],
) -> bool:
    for name, param in space.items():
        if isinstance(param, ChoiceParam):
            continue  # choice dims never narrow; ignored.
        low, high = box[name]
        width = high - low
        if isinstance(param, IntParam) and width < 1.0:
            continue  # frozen — counts as converged.
        orig = original_widths.get(name, 0.0)
        if orig <= 0:
            continue
        if width / orig >= epsilon:
            return False
    return True


__all__ = [
    "ChoiceParam",
    "ContinuousParam",
    "EvaluateBatchFn",
    "EvaluateFn",
    "GridSearcher",
    "IntParam",
    "LhsSearcher",
    "OptimizerResult",
    "ParamSet",
    "RandomParam",
    "RandomSearcher",
    "RecursiveGridDriver",
    "RecursiveGridSearcher",
    "ScoreFn",
    "Searcher",
    "SobolSearcher",
    "TPESearcher",
    "Trial",
    "cma_dedup_rate",
    "cma_resolve_popsize",
    "cma_unit_to_params",
    "de_bounds_and_integrality",
    "de_project_individual",
    "de_resolve_popsize",
    "de_sobol_init",
    "hooke_jeeves_propose",
    "optimize",
]
