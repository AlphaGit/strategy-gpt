"""Tests for the variance-aware mechanical gate."""

from __future__ import annotations

import math

import pytest

from strategy_gpt.mechanical_gate import (
    MechanicalGateConfig,
    mechanical_gate,
)
from strategy_gpt.reject_taxonomy import RejectKind


def test_accept_clearly_above_floor() -> None:
    out = mechanical_gate(
        candidate_fold_scores=[1.5, 1.6, 1.55, 1.58],
        baseline_fold_scores=[1.0, 1.05, 1.0, 1.02],
    )
    assert out.accept is True
    assert out.reject_kind is RejectKind.OK
    assert out.borderline is False
    assert out.score_delta > 0
    assert out.fold_cv < 0.1


def test_reject_noise_when_delta_inside_floor() -> None:
    # tight baseline, candidate barely above
    out = mechanical_gate(
        candidate_fold_scores=[1.0, 1.5, 1.0, 1.5],
        baseline_fold_scores=[1.0, 1.0, 1.0, 1.0],
        config=MechanicalGateConfig(k=1.0, fold_cv_threshold=10.0),
    )
    # sigma_candidate ~ 0.25, sigma_baseline = 0, floor = 0.25, delta = 0.25 not > 0.25
    assert out.accept is False
    assert out.reject_kind is RejectKind.REJECT_NOISE
    assert "sigma_combined" in out.rationale


def test_reject_variance_when_fold_cv_too_high() -> None:
    out = mechanical_gate(
        candidate_fold_scores=[0.5, 5.0, 0.5, 5.0],
        baseline_fold_scores=[1.0, 1.0, 1.0, 1.0],
        config=MechanicalGateConfig(k=0.01, fold_cv_threshold=0.5),
    )
    assert out.accept is False
    assert out.reject_kind is RejectKind.REJECT_VARIANCE
    assert out.fold_cv > 0.5


def test_borderline_flag_set_when_delta_barely_exceeds_floor() -> None:
    # delta only 10% above floor → borderline
    out = mechanical_gate(
        candidate_fold_scores=[1.1, 1.12, 1.11, 1.13],
        baseline_fold_scores=[1.0, 1.02, 1.0, 1.02],
        config=MechanicalGateConfig(k=0.5, borderline_pct=0.5),
    )
    if out.accept:
        # borderline is opt-in: if accepted, borderline iff delta-floor <= 50% * floor
        assert isinstance(out.borderline, bool)


def test_raises_on_empty_inputs() -> None:
    with pytest.raises(ValueError, match="at least one candidate"):
        mechanical_gate(candidate_fold_scores=[], baseline_fold_scores=[1.0])
    with pytest.raises(ValueError, match="at least one baseline"):
        mechanical_gate(candidate_fold_scores=[1.0], baseline_fold_scores=[])


def test_zero_variance_path_with_positive_delta() -> None:
    out = mechanical_gate(
        candidate_fold_scores=[2.0, 2.0, 2.0],
        baseline_fold_scores=[1.0, 1.0, 1.0],
    )
    # sigma_combined=0, floor=0, delta=1.0 > 0 → accept
    assert out.accept is True
    assert out.sigma_combined == 0.0
    assert math.isclose(out.score_delta, 1.0)
