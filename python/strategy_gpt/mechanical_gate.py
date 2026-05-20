"""Mechanical gate — deterministic, variance-aware accept floor.

Implements ``hypothesis-loop::mechanical-gate-is-a-hard-floor``: a
candidate that survives build, smoke, schema, and the mini-optimize
pass must clear two checks before the verdict-critique LLM is even
invoked:

1. **Score floor** — ``(candidate_score - baseline_score) > k * sigma_combined``
   where ``sigma_combined = sqrt(sigma_candidate^2 + sigma_baseline^2)`` over the
   per-fold best scores.
2. **Variance floor** — per-fold coefficient of variation
   ``cv = std(fold_scores) / mean(fold_scores)`` must stay below the
   configured threshold.

Both checks are deterministic and run without an LLM call. A
mechanical-gate rejection MUST NOT be overridable by any downstream
node (see ADR 0020 — comparative-falsification-variance-aware-epsilon).

A borderline flag is emitted when the candidate clears the score floor
by less than ``borderline_pct`` of the gap; the verdict-critique node
reads the flag and applies stricter scrutiny but cannot reverse a hard
reject.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .reject_taxonomy import RejectKind, noise_rationale, variance_rationale


@dataclass(frozen=True, slots=True)
class MechanicalGateConfig:
    """Configurable thresholds for the gate.

    ``k`` defaults to ``1.0`` (~68% confidence under Gaussian noise).
    ``fold_cv_threshold`` defaults to ``0.5`` (per the spec). Both are
    tunable; an operator can tighten ``k`` to 2.0 (~95%) or loosen
    ``fold_cv_threshold`` for naturally noisy objectives like drawdown.
    """

    k: float = 1.0
    fold_cv_threshold: float = 0.5
    borderline_pct: float = 0.20


@dataclass(frozen=True, slots=True)
class MechanicalGateOutcome:
    """Result of running the mechanical gate over per-fold best scores.

    ``accept`` is ``True`` only when both checks pass. ``reject_kind`` is
    one of ``ok`` / ``reject_noise`` / ``reject_variance``; the rationale
    string mirrors the structured payload persisted to
    :class:`DecisionRecord.evidence`. ``borderline`` is propagated even
    on accepts so verdict-critique can read it.

    Statistics are surfaced verbatim so the operator can audit a
    rejection from the ledger without re-running the optimize pass.
    """

    accept: bool
    reject_kind: RejectKind
    rationale: str
    borderline: bool
    candidate_score: float
    baseline_score: float
    score_delta: float
    sigma_candidate: float
    sigma_baseline: float
    sigma_combined: float
    fold_cv: float
    k: float
    fold_cv_threshold: float


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _stddev(xs: Sequence[float]) -> float:
    """Population standard deviation. Returns 0 when n < 2.

    Population (not sample) so two folds with identical scores produce
    zero variance — the gate then trivially accepts on the score floor
    if the delta is positive at all. This matches the spec's
    ``sigma_combined`` definition which carries no Bessel correction.
    """
    if len(xs) < 2:  # noqa: PLR2004 — stddev undefined for n<2
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))


def mechanical_gate(
    *,
    candidate_fold_scores: Sequence[float],
    baseline_fold_scores: Sequence[float],
    config: MechanicalGateConfig | None = None,
) -> MechanicalGateOutcome:
    """Apply the variance-aware score floor + per-fold CV check.

    Both fold-score lists must be non-empty. The candidate's aggregate
    is the mean of its per-fold best scores; the baseline aggregate is
    likewise the mean of its recorded per-fold scores. See the module
    docstring for the exact failure-mode taxonomy.
    """
    if not candidate_fold_scores:
        msg = "mechanical_gate requires at least one candidate fold score"
        raise ValueError(msg)
    if not baseline_fold_scores:
        msg = "mechanical_gate requires at least one baseline fold score"
        raise ValueError(msg)

    cfg = config if config is not None else MechanicalGateConfig()
    cand_mean = _mean(candidate_fold_scores)
    base_mean = _mean(baseline_fold_scores)
    sigma_cand = _stddev(candidate_fold_scores)
    sigma_base = _stddev(baseline_fold_scores)
    sigma_combined = math.sqrt(sigma_cand**2 + sigma_base**2)
    delta = cand_mean - base_mean

    fold_cv = sigma_cand / abs(cand_mean) if cand_mean != 0.0 else math.inf

    # Variance check fires first so a high-variance candidate that
    # *happens* to clear the score floor is still rejected. The spec's
    # wording is "both must pass" — order of evaluation does not change
    # the outcome, only the reject kind reported, and reporting variance
    # is the more actionable signal for the next-iteration generate.
    if fold_cv > cfg.fold_cv_threshold:
        rationale = variance_rationale(fold_cv=fold_cv, threshold=cfg.fold_cv_threshold)
        return MechanicalGateOutcome(
            accept=False,
            reject_kind=RejectKind.REJECT_VARIANCE,
            rationale=rationale.summary,
            borderline=False,
            candidate_score=cand_mean,
            baseline_score=base_mean,
            score_delta=delta,
            sigma_candidate=sigma_cand,
            sigma_baseline=sigma_base,
            sigma_combined=sigma_combined,
            fold_cv=fold_cv,
            k=cfg.k,
            fold_cv_threshold=cfg.fold_cv_threshold,
        )

    floor = cfg.k * sigma_combined
    if delta <= floor:
        rationale = noise_rationale(
            score=cand_mean,
            baseline_score=base_mean,
            sigma_combined=sigma_combined,
            k=cfg.k,
        )
        return MechanicalGateOutcome(
            accept=False,
            reject_kind=RejectKind.REJECT_NOISE,
            rationale=rationale.summary,
            borderline=False,
            candidate_score=cand_mean,
            baseline_score=base_mean,
            score_delta=delta,
            sigma_candidate=sigma_cand,
            sigma_baseline=sigma_base,
            sigma_combined=sigma_combined,
            fold_cv=fold_cv,
            k=cfg.k,
            fold_cv_threshold=cfg.fold_cv_threshold,
        )

    # Pass with optional borderline flag. ``floor`` is the absolute
    # threshold the delta must exceed; "borderline" means we exceeded
    # it by no more than ``borderline_pct * floor``. When the floor is
    # exactly zero (zero combined variance) we treat any positive
    # delta as decidedly non-borderline.
    borderline = bool(floor > 0.0 and (delta - floor) <= cfg.borderline_pct * floor)
    return MechanicalGateOutcome(
        accept=True,
        reject_kind=RejectKind.OK,
        rationale=(
            f"score floor passed: delta {delta:+.4f} > {cfg.k:.2f} * sigma_combined "
            f"({sigma_combined:.4f}); fold_cv {fold_cv:.4f} <= {cfg.fold_cv_threshold:.2f}"
        ),
        borderline=borderline,
        candidate_score=cand_mean,
        baseline_score=base_mean,
        score_delta=delta,
        sigma_candidate=sigma_cand,
        sigma_baseline=sigma_base,
        sigma_combined=sigma_combined,
        fold_cv=fold_cv,
        k=cfg.k,
        fold_cv_threshold=cfg.fold_cv_threshold,
    )


def mechanical_gate_node(
    state: object,
    *,
    candidate_fold_scores: Sequence[float],
    baseline_fold_scores: Sequence[float],
    config: MechanicalGateConfig | None = None,
) -> MechanicalGateOutcome:
    """LangGraph-style node wrapper.

    Today the gate operates on inputs the caller has already prepared
    (per-fold scores from the mini-optimize pass and the baseline
    cache). State threading is a thin layer — full StateGraph wiring
    lands in Phase D (task 4.1) — so this wrapper exists for symmetry
    with the other ``*_node`` callables.
    """
    del state  # explicitly unused — node signature kept symmetric
    return mechanical_gate(
        candidate_fold_scores=candidate_fold_scores,
        baseline_fold_scores=baseline_fold_scores,
        config=config,
    )


__all__ = [
    "MechanicalGateConfig",
    "MechanicalGateOutcome",
    "mechanical_gate",
    "mechanical_gate_node",
]
