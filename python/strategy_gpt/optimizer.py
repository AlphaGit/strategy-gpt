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
# Driver
# ---------------------------------------------------------------------------


EvaluateFn = Callable[[ParamSet], MetricsDict]
"""Caller-supplied: run the candidate (e.g. submit to engine, aggregate folds)
and return its metrics dict. The optimizer treats this as a black box; in
production it dispatches a walk-forward `BatchSpec` to the engine."""

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


__all__ = [
    "ChoiceParam",
    "ContinuousParam",
    "EvaluateFn",
    "GridSearcher",
    "IntParam",
    "OptimizerResult",
    "ParamSet",
    "RandomParam",
    "RandomSearcher",
    "ScoreFn",
    "Searcher",
    "TPESearcher",
    "Trial",
    "optimize",
]
