"""Optimizer driver + searcher tests.

Pure-Python; no native extension required. A synthetic objective lets us
verify determinism and best-trial selection without touching the engine.
"""

from __future__ import annotations

from typing import Any

from strategy_gpt.optimizer import (
    ChoiceParam,
    ContinuousParam,
    GridSearcher,
    IntParam,
    RandomSearcher,
    optimize,
)
from strategy_gpt.types import EvaluationOutcome

# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------


def test_grid_searcher_enumerates_cartesian_product() -> None:
    grid = GridSearcher({"a": [1, 2], "b": [10, 20, 30]})
    candidates = list(grid.candidates())
    assert grid.count() == 6
    assert len(candidates) == 6
    # Order is stable: outer key varies slowest (Python itertools.product order).
    assert candidates[0] == {"a": 1, "b": 10}
    assert candidates[-1] == {"a": 2, "b": 30}


def test_grid_searcher_unique_combinations() -> None:
    grid = GridSearcher({"x": [1, 2, 3], "y": [4, 5]})
    seen = {tuple(sorted(c.items())) for c in grid.candidates()}
    assert len(seen) == 6


# ---------------------------------------------------------------------------
# Random search
# ---------------------------------------------------------------------------


def test_random_searcher_determinism() -> None:
    space = {
        "lr": ContinuousParam(low=0.001, high=0.1, log=True),
        "n": IntParam(low=5, high=15),
        "mode": ChoiceParam(choices=["a", "b", "c"]),
    }
    a = list(RandomSearcher(space=space, n_iter=20, seed=42).candidates())
    b = list(RandomSearcher(space=space, n_iter=20, seed=42).candidates())
    assert a == b
    # Different seed → different sequence (extremely likely).
    c = list(RandomSearcher(space=space, n_iter=20, seed=43).candidates())
    assert a != c


def test_random_searcher_respects_n_iter() -> None:
    space = {"x": IntParam(low=0, high=100)}
    cands = list(RandomSearcher(space=space, n_iter=7, seed=1).candidates())
    assert len(cands) == 7


def test_random_searcher_continuous_range_bounded() -> None:
    space = {"v": ContinuousParam(low=0.0, high=1.0)}
    for c in RandomSearcher(space=space, n_iter=200, seed=0).candidates():
        assert 0.0 <= c["v"] <= 1.0


def test_random_searcher_log_uniform_stays_in_range() -> None:
    space = {"lr": ContinuousParam(low=1e-4, high=1e-1, log=True)}
    for c in RandomSearcher(space=space, n_iter=200, seed=0).candidates():
        assert 1e-4 <= c["lr"] <= 1e-1


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _evaluate_quadratic(params: dict[str, Any]) -> dict[str, float]:
    """Synthetic objective: peak at x=5 across an integer 1..10 grid."""
    x = float(params["x"])
    return {"sharpe": -((x - 5.0) ** 2)}


def _score_from_sharpe(metrics: dict[str, float]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=metrics["sharpe"] >= -1.0,
        score=metrics["sharpe"],
        violations=[],
        soft_misses=[],
    )


def test_optimize_grid_picks_best_accepted_trial() -> None:
    result = optimize(
        searcher=GridSearcher({"x": list(range(1, 11))}),
        evaluate=_evaluate_quadratic,
        score=_score_from_sharpe,
    )
    assert len(result.trials) == 10
    # Peak is at x=5 (sharpe=0). x=4 and x=6 are also accepted (sharpe=-1).
    assert result.best is not None
    assert result.best.params == {"x": 5}
    assert result.best.outcome.score == 0.0


def test_optimize_oos_min_score_gate_rejects_low_scores() -> None:
    # oos_min_score=-0.5 only accepts x=5 (score=0.0); x=4 (-1) and x=6 (-1)
    # would otherwise pass `_score_from_sharpe` but fall under the gate.
    result = optimize(
        searcher=GridSearcher({"x": list(range(1, 11))}),
        evaluate=_evaluate_quadratic,
        score=_score_from_sharpe,
        oos_min_score=-0.5,
    )
    accepted = [t for t in result.trials if t.accepted]
    assert len(accepted) == 1
    assert accepted[0].params == {"x": 5}
    assert result.rejected_count == 9


def test_optimize_returns_none_best_when_all_rejected() -> None:
    """All-rejecting score function → `best` is None."""

    def reject_all(_: dict[str, float]) -> EvaluationOutcome:
        return EvaluationOutcome(accepted=False, score=0.0, violations=["x"], soft_misses=[])

    result = optimize(
        searcher=GridSearcher({"x": [1, 2, 3]}),
        evaluate=_evaluate_quadratic,
        score=reject_all,
    )
    assert result.best is None
    assert result.rejected_count == 3


def test_optimize_preserves_candidate_order() -> None:
    """Trial list reflects searcher's submission order (determinism check)."""
    result = optimize(
        searcher=GridSearcher({"x": [3, 1, 2]}),
        evaluate=_evaluate_quadratic,
        score=_score_from_sharpe,
    )
    assert [t.params["x"] for t in result.trials] == [3, 1, 2]
