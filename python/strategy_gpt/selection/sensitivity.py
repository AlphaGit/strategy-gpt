"""Parameter-sensitivity (robust) score over the trial history.

For each candidate, compute the mean minus lambda * std of the objective
scores over the k nearest already-evaluated trials in min-max-normalized
parameter space. The candidate's own score participates in the
neighborhood mean (self-inclusion).

References:
- Lopez de Prado (2018), *Advances in Financial Machine Learning*,
  ch. 11-12.
- Pardo (2008), *The Evaluation and Optimization of Trading
  Strategies*, ch. 9.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

from pydantic import BaseModel, ConfigDict, Field


class SensitivityKnobs(BaseModel):
    """Tunable knobs for parameter-sensitivity scoring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    neighborhood_k: int = Field(default=8, ge=1)
    penalty: float = Field(default=1.0, ge=0.0)


@dataclass(frozen=True)
class TrialPoint:
    """One point in parameter space with its observed score."""

    params: Mapping[str, Any]
    score: float


@dataclass(frozen=True)
class SensitivityResult:
    """Per-candidate robust-score outcome."""

    raw_score: float
    neighborhood_mean: float
    neighborhood_std: float
    robust_score: float
    neighbors_used: int


def _collect_param_keys(points: Sequence[TrialPoint]) -> list[str]:
    keys: dict[str, None] = {}
    for p in points:
        for k in p.params:
            keys.setdefault(k, None)
    return list(keys)


def _is_numeric(value: Any) -> TypeGuard[int | float]:  # noqa: ANN401 — heterogeneous params.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _min_max_ranges(
    points: Sequence[TrialPoint], keys: Sequence[str]
) -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    for k in keys:
        numeric_vals: list[float] = []
        all_numeric = True
        for p in points:
            v = p.params.get(k)
            if v is None:
                continue
            if _is_numeric(v):
                numeric_vals.append(float(v))
            else:
                all_numeric = False
                break
        if all_numeric and numeric_vals:
            lo, hi = min(numeric_vals), max(numeric_vals)
            ranges[k] = (lo, hi)
    return ranges


def _distance(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    keys: Sequence[str],
    ranges: Mapping[str, tuple[float, float]],
) -> float:
    """Euclidean distance over numeric dims (min-max normalized); 0/1 for categoricals."""
    sq = 0.0
    for k in keys:
        va = a.get(k)
        vb = b.get(k)
        if k in ranges and _is_numeric(va) and _is_numeric(vb):
            lo, hi = ranges[k]
            span = hi - lo
            if span <= 0.0:
                continue
            d = (float(va) - float(vb)) / span
            sq += d * d
        elif va != vb:
            sq += 1.0
    return math.sqrt(sq)


def compute_sensitivity(
    candidate: TrialPoint,
    history: Sequence[TrialPoint],
    knobs: SensitivityKnobs,
) -> SensitivityResult:
    """Robust score for one candidate against the trial history.

    The candidate is self-included in the neighborhood: the search
    process already evaluated it, and excluding it would force the score
    to depend on a strictly different point set than ``score`` measures.
    """
    keys = _collect_param_keys([candidate, *history])
    ranges = _min_max_ranges([candidate, *history], keys)
    # Score-bearing points only; trials whose score is non-finite (NaN /
    # -inf rejection) are excluded from neighborhood statistics.
    pool: list[TrialPoint] = [p for p in history if math.isfinite(p.score)]
    # Self-inclusion: prepend the candidate so it participates in the mean.
    if math.isfinite(candidate.score):
        pool = [candidate, *pool]
    if not pool:
        return SensitivityResult(
            raw_score=candidate.score,
            neighborhood_mean=candidate.score,
            neighborhood_std=0.0,
            robust_score=candidate.score,
            neighbors_used=0,
        )
    distances = [(_distance(candidate.params, p.params, keys, ranges), p.score) for p in pool]
    distances.sort(key=lambda t: t[0])
    k = min(knobs.neighborhood_k, len(distances))
    chosen = [s for _, s in distances[:k]]
    mean = statistics.fmean(chosen)
    std = statistics.pstdev(chosen) if len(chosen) > 1 else 0.0
    robust = mean - knobs.penalty * std
    return SensitivityResult(
        raw_score=candidate.score,
        neighborhood_mean=mean,
        neighborhood_std=std,
        robust_score=robust,
        neighbors_used=k,
    )


__all__ = [
    "SensitivityKnobs",
    "SensitivityResult",
    "TrialPoint",
    "compute_sensitivity",
]
