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

Bayesian search (TPE) is a follow-up (`rewrite-architecture` task 11.4).
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
    "Trial",
    "optimize",
]
