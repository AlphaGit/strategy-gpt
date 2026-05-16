"""Selector orchestrator — PBO → DSR → sensitivity → decision.

Pure function of trial history + manifest knobs. Same inputs, same
outputs (byte-equal when serialized) so post-hoc reselection over the
same ``trials.parquet`` is deterministic.

Decision logic (per design §5):

- If ``PBO > pbo.threshold`` and ``force is False`` →
  ``rejected_pbo``. The candidate the configured ranking *would have*
  picked is recorded as ``would_have_picked`` for transparency.
- Else, rank top-K:
  - ``robust_objective: True`` → rank by sensitivity ``robust_score``.
  - else → rank by DSR descending, tie-break by raw primary score.
- Final = top-1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from .cscv import PboKnobs, PboResult, compute_pbo
from .dsr import DsrInput, DsrKnobs, DsrResult, compute_dsr_top_k
from .sensitivity import (
    SensitivityKnobs,
    SensitivityResult,
    TrialPoint,
    compute_sensitivity,
)


class SelectionStatus(StrEnum):
    """Final-decision outcome status."""

    ACCEPTED = "accepted"
    REJECTED_PBO = "rejected_pbo"
    REJECTED_CONSTRAINT = "rejected_constraint"


class SelectionKnobs(BaseModel):
    """Top-level selection-layer configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pbo: PboKnobs = PboKnobs()
    deflated_sharpe: DsrKnobs = DsrKnobs()
    sensitivity: SensitivityKnobs = SensitivityKnobs()


@dataclass(frozen=True)
class SelectionCandidate:
    """One candidate fed to the selection layer.

    The optimizer builds these from the cross-validation phase: each
    fold winner is one candidate whose ``per_fold_oos_primary`` is the
    primary-metric value on every fold's OOS slice.
    """

    trial_id: int
    params: dict[str, Any]
    aggregate_score: float
    aggregate_metrics: dict[str, float]
    per_fold_oos_primary: list[float]
    trade_count: int
    accepted: bool = True
    skew: float = 0.0
    kurt: float = 3.0


@dataclass(frozen=True)
class CandidateScores:
    """Per-candidate scores recorded for audit."""

    trial_id: int
    raw_score: float
    raw_sharpe: float
    dsr: DsrResult
    sensitivity: SensitivityResult


@dataclass(frozen=True)
class SelectionDecision:
    """Final outcome of the selection layer."""

    status: SelectionStatus
    best_trial_id: int | None
    would_have_picked_trial_id: int | None
    reason: str
    pbo: PboResult
    candidate_scores: list[CandidateScores]
    ranking: list[int]
    robust_objective: bool
    force_override: bool
    pbo_threshold: float
    effective_n: int
    history_size: int
    methodology: dict[str, str] = field(default_factory=dict)


SELECTION_METHODOLOGY: dict[str, str] = {
    "pbo": (
        "Bailey, Borwein, López de Prado, Zhu (2017), "
        "'The Probability of Backtest Overfitting', J. Computational Finance"
    ),
    "dsr": ("Bailey, López de Prado (2014), 'The Deflated Sharpe Ratio', J. Portfolio Management"),
    "sensitivity": (
        "López de Prado (2018), 'Advances in Financial Machine Learning' "
        "ch. 11-12 (Wiley); Pardo (2008), 'The Evaluation and Optimization "
        "of Trading Strategies' ch. 9 (Wiley)"
    ),
}


def _top_k_candidates(
    candidates: Sequence[SelectionCandidate], top_k: int
) -> list[SelectionCandidate]:
    """Sort candidates by aggregate_score descending and take the top-K."""
    ordered = sorted(
        candidates,
        key=lambda c: (c.accepted, c.aggregate_score),
        reverse=True,
    )
    return ordered[: max(top_k, 1)]


def _effective_n(
    candidates: Sequence[SelectionCandidate],
    history: Sequence[TrialPoint],
    mode: str,
) -> int:
    if mode == "trial_count":
        return max(len(history), len(candidates))
    seen: set[tuple[tuple[str, Any], ...]] = set()
    for p in history:
        key = tuple(sorted(p.params.items()))
        seen.add(key)
    for c in candidates:
        seen.add(tuple(sorted(c.params.items())))
    return max(len(seen), 1)


def run_selection(  # noqa: PLR0913 — composition of three configurable layers + overrides.
    candidates: Sequence[SelectionCandidate],
    history: Sequence[TrialPoint],
    knobs: SelectionKnobs,
    *,
    robust_objective: bool = False,
    force: bool = False,
    pbo_threshold_override: float | None = None,
    pbo_seed: int = 0,
) -> SelectionDecision:
    """Run the selection pipeline against a candidate set.

    ``history`` is the full evaluated-trial history (used by the
    sensitivity layer's k-NN). ``candidates`` are the top-level
    cross-validated candidates (typically one per fold winner).
    """
    if not candidates:
        return SelectionDecision(
            status=SelectionStatus.REJECTED_CONSTRAINT,
            best_trial_id=None,
            would_have_picked_trial_id=None,
            reason="no candidates supplied to selection layer",
            pbo=PboResult(pbo=0.0, n_splits=0, enumerated=True, seed=None, n_trials=0, n_folds=0),
            candidate_scores=[],
            ranking=[],
            robust_objective=robust_objective,
            force_override=force,
            pbo_threshold=(
                pbo_threshold_override
                if pbo_threshold_override is not None
                else knobs.pbo.threshold
            ),
            effective_n=0,
            history_size=len(history),
            methodology=dict(SELECTION_METHODOLOGY),
        )

    top_k = _top_k_candidates(candidates, knobs.pbo.top_k)
    matrix = [c.per_fold_oos_primary for c in top_k]
    pbo = (
        compute_pbo(matrix, knobs.pbo, seed=pbo_seed)
        if knobs.pbo.enabled
        else PboResult(
            pbo=0.0,
            n_splits=0,
            enumerated=True,
            seed=None,
            n_trials=len(top_k),
            n_folds=len(matrix[0]) if matrix else 0,
        )
    )

    effective_n = _effective_n(candidates, history, knobs.deflated_sharpe.effective_n)
    dsr_inputs = [
        DsrInput(
            sharpe=c.aggregate_metrics.get("sharpe", c.aggregate_score),
            trade_count=c.trade_count,
            skew=c.skew,
            kurt=c.kurt,
        )
        for c in top_k
    ]
    dsr_results: list[DsrResult] = (
        compute_dsr_top_k(dsr_inputs, effective_n=effective_n)
        if knobs.deflated_sharpe.enabled
        else [
            DsrResult(sharpe=d.sharpe, expected_max_sharpe=0.0, sharpe_variance=0.0, z=0.0, dsr=0.0)
            for d in dsr_inputs
        ]
    )

    sensitivity_results: list[SensitivityResult] = []
    if knobs.sensitivity.enabled:
        for c in top_k:
            tp = TrialPoint(params=c.params, score=c.aggregate_score)
            sensitivity_results.append(compute_sensitivity(tp, history, knobs.sensitivity))
    else:
        for c in top_k:
            sensitivity_results.append(
                SensitivityResult(
                    raw_score=c.aggregate_score,
                    neighborhood_mean=c.aggregate_score,
                    neighborhood_std=0.0,
                    robust_score=c.aggregate_score,
                    neighbors_used=0,
                )
            )

    candidate_scores = [
        CandidateScores(
            trial_id=c.trial_id,
            raw_score=c.aggregate_score,
            raw_sharpe=c.aggregate_metrics.get("sharpe", c.aggregate_score),
            dsr=d,
            sensitivity=s,
        )
        for c, d, s in zip(top_k, dsr_results, sensitivity_results, strict=True)
    ]

    def _per_fold_variance(c: SelectionCandidate) -> float:
        values = c.per_fold_oos_primary
        if len(values) < 2:  # noqa: PLR2004
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    # Lower per-fold variance breaks DSR / robust-score ties; we negate it so the
    # ranker still uses ``reverse=True``.
    var_keys = [-_per_fold_variance(c) for c in top_k]

    if robust_objective:
        ranked = sorted(
            range(len(candidate_scores)),
            key=lambda i: (
                top_k[i].accepted,
                candidate_scores[i].sensitivity.robust_score,
                candidate_scores[i].raw_score,
                var_keys[i],
            ),
            reverse=True,
        )
    else:
        ranked = sorted(
            range(len(candidate_scores)),
            key=lambda i: (
                top_k[i].accepted,
                candidate_scores[i].dsr.dsr,
                candidate_scores[i].raw_score,
                var_keys[i],
            ),
            reverse=True,
        )
    ranking_trial_ids = [candidate_scores[i].trial_id for i in ranked]

    threshold = (
        pbo_threshold_override if pbo_threshold_override is not None else knobs.pbo.threshold
    )

    top_idx = ranked[0] if ranked else None
    top_id = candidate_scores[top_idx].trial_id if top_idx is not None else None
    top_accepted = top_k[top_idx].accepted if top_idx is not None else False

    if knobs.pbo.enabled and pbo.pbo > threshold and not force:
        return SelectionDecision(
            status=SelectionStatus.REJECTED_PBO,
            best_trial_id=None,
            would_have_picked_trial_id=top_id,
            reason=f"PBO={pbo.pbo:.4f} > threshold={threshold:.4f}",
            pbo=pbo,
            candidate_scores=candidate_scores,
            ranking=ranking_trial_ids,
            robust_objective=robust_objective,
            force_override=force,
            pbo_threshold=threshold,
            effective_n=effective_n,
            history_size=len(history),
            methodology=dict(SELECTION_METHODOLOGY),
        )

    if top_idx is None or not top_accepted:
        return SelectionDecision(
            status=SelectionStatus.REJECTED_CONSTRAINT,
            best_trial_id=None,
            would_have_picked_trial_id=top_id,
            reason="no candidate satisfied objective constraints",
            pbo=pbo,
            candidate_scores=candidate_scores,
            ranking=ranking_trial_ids,
            robust_objective=robust_objective,
            force_override=force,
            pbo_threshold=threshold,
            effective_n=effective_n,
            history_size=len(history),
            methodology=dict(SELECTION_METHODOLOGY),
        )

    return SelectionDecision(
        status=SelectionStatus.ACCEPTED,
        best_trial_id=top_id,
        would_have_picked_trial_id=top_id,
        reason=(f"PBO={pbo.pbo:.4f} <= threshold={threshold:.4f}; forced override applied={force}")
        if force
        else f"PBO={pbo.pbo:.4f} <= threshold={threshold:.4f}",
        pbo=pbo,
        candidate_scores=candidate_scores,
        ranking=ranking_trial_ids,
        robust_objective=robust_objective,
        force_override=force,
        pbo_threshold=threshold,
        effective_n=effective_n,
        history_size=len(history),
        methodology=dict(SELECTION_METHODOLOGY),
    )


__all__ = [
    "SELECTION_METHODOLOGY",
    "CandidateScores",
    "SelectionCandidate",
    "SelectionDecision",
    "SelectionKnobs",
    "SelectionStatus",
    "run_selection",
]
