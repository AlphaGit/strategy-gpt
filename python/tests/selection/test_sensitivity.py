"""Unit tests for parameter-sensitivity scoring."""

from __future__ import annotations

from strategy_gpt.selection.sensitivity import (
    SensitivityKnobs,
    TrialPoint,
    compute_sensitivity,
)


def test_knife_edge_robust_score_below_raw() -> None:
    """Knife-edge: a high-score point surrounded by low-score points → robust ≪ raw."""
    knife_edge = TrialPoint(params={"x": 0.5, "y": 0.5}, score=10.0)
    # 100 evenly spaced points, all with low scores.
    history: list[TrialPoint] = []
    for i in range(10):
        for j in range(10):
            x = i / 9.0
            y = j / 9.0
            if (i, j) == (5, 5):  # don't duplicate the knife-edge.
                continue
            history.append(TrialPoint(params={"x": x, "y": y}, score=0.0))
    out = compute_sensitivity(knife_edge, history, SensitivityKnobs(neighborhood_k=8))
    assert out.raw_score == 10.0
    # Neighborhood mean: 1 high (self) + 7 low → 10/8 = 1.25; std is high; robust ≪ raw.
    assert out.robust_score < out.raw_score
    assert out.robust_score < 2.0


def test_plateau_robust_close_to_raw() -> None:
    """Plateau: candidate sits in a high-and-flat region → robust ≈ raw."""
    candidate = TrialPoint(params={"x": 0.5}, score=5.0)
    history = [
        TrialPoint(params={"x": 0.5 + i * 0.01}, score=5.0 + i * 0.01)
        for i in range(-4, 5)
        if i != 0
    ]
    out = compute_sensitivity(candidate, history, SensitivityKnobs(neighborhood_k=8))
    assert abs(out.robust_score - out.raw_score) < 0.2


def test_categorical_distance_used() -> None:
    """Categorical dim contributes 0/1 distance."""
    a = TrialPoint(params={"mode": "long", "x": 0.5}, score=10.0)
    same_cat = TrialPoint(params={"mode": "long", "x": 0.5}, score=9.0)
    diff_cat = TrialPoint(params={"mode": "short", "x": 0.5}, score=0.0)
    out = compute_sensitivity(
        a,
        [same_cat, diff_cat],
        SensitivityKnobs(neighborhood_k=2, penalty=0.0),
    )
    # neighborhood_k=2 → self + nearest = {a, same_cat} (diff_cat is further).
    # Wait — with k=2 we pick the 2 nearest of {self, same_cat, diff_cat} pool.
    # Distances: self=0, same_cat=0 (params equal), diff_cat=1.
    # k=2: pick the two zero-distance points; mean = (10+9)/2 = 9.5.
    assert abs(out.neighborhood_mean - 9.5) < 1e-9


def test_self_inclusion_in_neighborhood() -> None:
    """Empty history with k=1 → robust score equals raw (self-only neighborhood)."""
    a = TrialPoint(params={"x": 0.5}, score=4.2)
    out = compute_sensitivity(a, [], SensitivityKnobs(neighborhood_k=1))
    assert out.raw_score == 4.2
    assert out.neighborhood_mean == 4.2
    assert out.neighborhood_std == 0.0
    assert out.robust_score == 4.2


def test_non_finite_scores_skipped() -> None:
    """History entries with -inf score don't pollute the neighborhood."""
    a = TrialPoint(params={"x": 0.5}, score=5.0)
    bad = TrialPoint(params={"x": 0.51}, score=float("-inf"))
    good = TrialPoint(params={"x": 0.52}, score=5.5)
    out = compute_sensitivity(a, [bad, good], SensitivityKnobs(neighborhood_k=8))
    # Only self + good participate.
    assert out.neighbors_used == 2
    assert abs(out.neighborhood_mean - 5.25) < 1e-9
