"""Combinatorially Symmetric Cross-Validation → PBO.

Reference: Bailey, Borwein, López de Prado, Zhu (2017),
"The Probability of Backtest Overfitting", J. Computational Finance.

Setup: given an ``(N, S)`` matrix ``M`` of per-fold OOS metric values
(N = top-K trials, S = folds), partition the S folds into every
combinatorial split of two equal halves ``(A, B)`` of size ``S/2``. For
each split, identify the IS-best trial ``i*`` from the A-half means, then
record its OOS rank on the B-half. PBO is the fraction of splits in which
``i*`` lands in the bottom half of the OOS ranking.

For ``S ≤ 16`` the layer enumerates every :math:`\\binom{S}{S/2}` split.
Beyond that, splits are sampled with a seeded RNG (the seed is recorded in
the manifest so the run is replayable).
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class PboKnobs(BaseModel):
    """Tunable knobs for the PBO computation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    top_k: int = Field(default=50, ge=2)
    max_splits: int = Field(default=4096, ge=1)


@dataclass(frozen=True)
class PboResult:
    """Outcome of a CSCV/PBO computation."""

    pbo: float
    n_splits: int
    enumerated: bool
    seed: int | None
    n_trials: int
    n_folds: int


_ENUMERATE_MAX_FOLDS = 16


def enumerate_splits(s: int) -> Iterator[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Yield every (A, B) split of `range(s)` into two equal halves.

    The CSCV paper treats (A, B) and (B, A) as distinct splits (the IS-best
    drawn from A may differ from the IS-best drawn from B), so the iterator
    yields :math:`\\binom{S}{S/2}` splits, not :math:`\\binom{S}{S/2}/2`.
    """
    if s % 2 != 0:
        msg = f"enumerate_splits: s must be even, got {s}"
        raise ValueError(msg)
    half = s // 2
    all_idx = range(s)
    for combo in itertools.combinations(all_idx, half):
        a = combo
        b = tuple(i for i in all_idx if i not in set(combo))
        yield (a, b)


def sample_splits(
    s: int, n_splits: int, *, seed: int
) -> Iterator[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Monte Carlo sample `n_splits` distinct equal-half partitions.

    Each yielded ``(A, B)`` is an ordered split (A used as IS, B as OOS),
    matching :func:`enumerate_splits`.
    """
    if s % 2 != 0:
        msg = f"sample_splits: s must be even, got {s}"
        raise ValueError(msg)
    half = s // 2
    rng = random.Random(seed)  # noqa: S311 — CSCV sampling is statistical, not cryptographic.
    seen: set[tuple[int, ...]] = set()
    all_idx = list(range(s))
    while len(seen) < n_splits:
        a = tuple(sorted(rng.sample(all_idx, half)))
        if a in seen:
            continue
        seen.add(a)
        a_set = set(a)
        b = tuple(i for i in all_idx if i not in a_set)
        yield (a, b)


def _rank(value: float, values: Sequence[float]) -> float:
    """1-indexed average rank of `value` within `values` (fractional ties)."""
    less = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return less + (equal + 1) / 2.0


def compute_pbo(
    matrix: Sequence[Sequence[float]],
    knobs: PboKnobs,
    *,
    seed: int = 0,
) -> PboResult:
    """Estimate PBO from the (N, S) per-fold OOS metric matrix.

    Drops the trailing fold when S is odd; degenerate cases (N<2 or S<2)
    yield ``pbo=0.0`` so the selection layer does not reject runs with
    too few folds to estimate overfitting.
    """
    if not matrix:
        return PboResult(
            pbo=0.0,
            n_splits=0,
            enumerated=True,
            seed=None,
            n_trials=0,
            n_folds=0,
        )
    n = len(matrix)
    s = len(matrix[0])
    for row in matrix:
        if len(row) != s:
            msg = "compute_pbo: rows must have equal length"
            raise ValueError(msg)
    if n < 2 or s < 2:  # noqa: PLR2004
        return PboResult(
            pbo=0.0,
            n_splits=0,
            enumerated=True,
            seed=None,
            n_trials=n,
            n_folds=s,
        )
    if s % 2 != 0:
        s -= 1
        matrix = [row[:s] for row in matrix]
    total_combos = math.comb(s, s // 2)
    if s <= _ENUMERATE_MAX_FOLDS:
        splits = list(enumerate_splits(s))
        enumerated = True
        seed_used: int | None = None
    else:
        target = min(knobs.max_splits, total_combos)
        splits = list(sample_splits(s, target, seed=seed))
        enumerated = False
        seed_used = seed

    overfit = 0
    n_evaluated = 0
    for a_idx, b_idx in splits:
        is_means = [sum(matrix[i][j] for j in a_idx) / len(a_idx) for i in range(n)]
        i_star = max(range(n), key=lambda i: is_means[i])
        oos_values = [sum(matrix[i][j] for j in b_idx) / len(b_idx) for i in range(n)]
        rank = _rank(oos_values[i_star], oos_values)
        omega = rank / (n + 1)
        if omega <= 0.0 or omega >= 1.0:
            logit = -math.inf if omega <= 0.5 else math.inf  # noqa: PLR2004
        else:
            logit = math.log(omega / (1 - omega))
        if logit < 0:
            overfit += 1
        n_evaluated += 1
    pbo = overfit / n_evaluated if n_evaluated else 0.0
    return PboResult(
        pbo=pbo,
        n_splits=n_evaluated,
        enumerated=enumerated,
        seed=seed_used,
        n_trials=n,
        n_folds=s,
    )


__all__ = [
    "PboKnobs",
    "PboResult",
    "compute_pbo",
    "enumerate_splits",
    "sample_splits",
]
