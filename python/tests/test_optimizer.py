"""Optimizer driver + searcher tests.

Pure-Python; no native extension required. A synthetic objective lets us
verify determinism and best-trial selection without touching the engine.
"""

from __future__ import annotations

from typing import Any

import pytest

from strategy_gpt.optimizer import (
    ChoiceParam,
    ContinuousParam,
    GridSearcher,
    IntParam,
    RandomSearcher,
    TPESearcher,
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


# ---------------------------------------------------------------------------
# TPE search
# ---------------------------------------------------------------------------


def _evaluate_unimodal_continuous(params: dict[str, Any]) -> dict[str, float]:
    """Smooth unimodal objective on a continuous range, peak at x=5.0."""
    x = float(params["x"])
    return {"sharpe": -((x - 5.0) ** 2)}


def _score_passthrough(metrics: dict[str, float]) -> EvaluationOutcome:
    return EvaluationOutcome(accepted=True, score=metrics["sharpe"], violations=[], soft_misses=[])


def test_tpe_determinism() -> None:
    space = {"x": ContinuousParam(low=0.0, high=10.0)}
    a = TPESearcher(space=space, n_iter=30, seed=7).search(
        evaluate=_evaluate_unimodal_continuous, score=_score_passthrough
    )
    b = TPESearcher(space=space, n_iter=30, seed=7).search(
        evaluate=_evaluate_unimodal_continuous, score=_score_passthrough
    )
    assert [t.params for t in a.trials] == [t.params for t in b.trials]
    assert a.best is not None
    assert b.best is not None
    assert a.best.params == b.best.params
    assert a.best.outcome.score == b.best.outcome.score


def test_tpe_converges_on_unimodal_objective() -> None:
    """TPE should outperform random search on a smooth 1-D unimodal target.

    Compares best-so-far score after the same number of iterations; with the
    peak at x=5.0 and a wide range [0, 10], TPE should pull samples toward
    the centre after the startup phase. A loose bound prevents flakiness.
    """
    space = {"x": ContinuousParam(low=0.0, high=10.0)}
    tpe = TPESearcher(space=space, n_iter=60, seed=11, n_startup_trials=10).search(
        evaluate=_evaluate_unimodal_continuous, score=_score_passthrough
    )
    rand = optimize(
        searcher=RandomSearcher(space=space, n_iter=60, seed=11),
        evaluate=_evaluate_unimodal_continuous,
        score=_score_passthrough,
    )
    assert tpe.best is not None
    assert rand.best is not None
    # TPE's best score should land close to the optimum (>= -0.5).
    assert tpe.best.outcome.score >= -0.5
    # And it should not be worse than random search at the same budget.
    assert tpe.best.outcome.score >= rand.best.outcome.score - 0.1


def test_tpe_handles_mixed_param_types() -> None:
    space = {
        "lr": ContinuousParam(low=1e-3, high=1.0, log=True),
        "depth": IntParam(low=1, high=10),
        "mode": ChoiceParam(choices=["a", "b", "c"]),
    }

    def evaluate(params: dict[str, Any]) -> dict[str, float]:
        # Synthetic objective rewards mode=="b" and large depth.
        score = float(params["depth"]) + (5.0 if params["mode"] == "b" else 0.0)
        return {"sharpe": score}

    result = TPESearcher(space=space, n_iter=25, seed=3).search(
        evaluate=evaluate, score=_score_passthrough
    )
    assert len(result.trials) == 25
    # Sanity: every trial's params match the space's shape.
    for trial in result.trials:
        assert set(trial.params.keys()) == {"lr", "depth", "mode"}
        assert 1e-3 <= trial.params["lr"] <= 1.0
        assert 1 <= trial.params["depth"] <= 10
        assert trial.params["mode"] in {"a", "b", "c"}


def test_tpe_rejects_invalid_gamma() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0)}
    with pytest.raises(ValueError, match="gamma"):
        TPESearcher(space=space, n_iter=5, seed=0, gamma=0.0).search(
            evaluate=_evaluate_unimodal_continuous, score=_score_passthrough
        )


def test_tpe_oos_min_score_gate_marks_low_scores_rejected() -> None:
    space = {"x": ContinuousParam(low=0.0, high=10.0)}
    result = TPESearcher(space=space, n_iter=20, seed=5).search(
        evaluate=_evaluate_unimodal_continuous,
        score=_score_passthrough,
        oos_min_score=0.0,  # only the exact peak passes
    )
    accepted = [t for t in result.trials if t.accepted]
    # Almost certainly no trial achieves the exact peak; gate rejects all.
    assert len(accepted) == 0
    assert result.rejected_count == 20
    assert result.best is None
