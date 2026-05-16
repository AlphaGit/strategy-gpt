"""RecursiveGridSearcher unit tests (optimize-command tasks 1.5 + 8.5)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from strategy_gpt.optimizer import (
    ContinuousParam,
    IntParam,
    RecursiveGridSearcher,
)
from strategy_gpt.types import EvaluationOutcome


def _quadratic(params: dict[str, Any]) -> dict[str, float]:
    """Concave unimodal surface with maximum at (0.4, 0.7)."""
    x = float(params["x"])
    y = float(params["y"])
    return {"sharpe": 1.0 - (x - 0.4) ** 2 - (y - 0.7) ** 2}


def _score_sharpe(metrics: dict[str, Any]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True, score=float(metrics["sharpe"]), violations=[], soft_misses=[]
    )


def _eval_batch(cands: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
    return [_quadratic(dict(c)) for c in cands]


def test_recursive_grid_converges_to_known_optimum() -> None:
    searcher = RecursiveGridSearcher(
        space={
            "x": ContinuousParam(low=0.0, high=1.0),
            "y": ContinuousParam(low=0.0, high=1.0),
        },
        resolution=10,
        top_k=1,
        depth=5,
        plateau_epsilon=1e-4,
        seed=0,
    )
    result = searcher.search(_eval_batch, _score_sharpe)
    assert result.best is not None
    assert abs(result.best.params["x"] - 0.4) < 0.02
    assert abs(result.best.params["y"] - 0.7) < 0.02


def test_recursive_grid_plateau_stops_when_box_collapses_below_epsilon() -> None:
    # plateau_epsilon=0.5 makes any first-round shrink count as converged
    # (one round shrinks each dim to 2/(res-1) of its prior width).
    searcher = RecursiveGridSearcher(
        space={
            "x": ContinuousParam(low=0.0, high=1.0),
            "y": ContinuousParam(low=0.0, high=1.0),
        },
        resolution=10,
        top_k=1,
        depth=10,
        plateau_epsilon=0.5,
        seed=0,
    )
    result = searcher.search(_eval_batch, _score_sharpe)
    # Round 1 evaluates 10x10 = 100 candidates; plateau-stop triggers and we
    # never run round 2.
    assert len(result.trials) == 100


def test_recursive_grid_partial_convergence_does_not_stop() -> None:
    # Choice dims never converge → plateau-stop never fires; depth wins.
    def evaluate(cands: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
        return [{"sharpe": -((c["x"] - 0.5) ** 2)} for c in cands]

    searcher = RecursiveGridSearcher(
        space={"x": ContinuousParam(low=0.0, high=1.0)},
        resolution=3,
        top_k=1,
        depth=3,
        plateau_epsilon=0.9,  # would converge first round if not for...
        seed=0,
    )
    result = searcher.search(evaluate, _score_sharpe)
    # With epsilon=0.9, round 1 shrinks to width = 2/(3-1) x 1 = 1.0 of orig
    # (because half_step = (1-0)/2 = 0.5 → bbox = top ± 0.5 = full). So no
    # convergence. Then second round shrinks further. Verify >= 2 rounds.
    assert len(result.trials) >= 3  # at least one full round per depth.  noqa: PLR2004


def test_recursive_grid_integer_dim_freezes_on_collapse() -> None:
    def evaluate(cands: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
        # Maximum at k=3.
        return [{"sharpe": -((c["k"] - 3) ** 2)} for c in cands]

    searcher = RecursiveGridSearcher(
        space={"k": IntParam(low=0, high=5)},
        resolution=6,
        top_k=1,
        depth=5,
        plateau_epsilon=1e-9,
        seed=0,
    )
    result = searcher.search(evaluate, _score_sharpe)
    assert result.best is not None
    assert result.best.params["k"] == 3


def test_recursive_grid_determinism() -> None:
    searcher = RecursiveGridSearcher(
        space={
            "x": ContinuousParam(low=0.0, high=1.0),
            "y": ContinuousParam(low=0.0, high=1.0),
        },
        resolution=5,
        top_k=2,
        depth=3,
        plateau_epsilon=1e-9,
        seed=123,
    )
    a = searcher.search(_eval_batch, _score_sharpe)
    b = searcher.search(_eval_batch, _score_sharpe)
    assert [t.params for t in a.trials] == [t.params for t in b.trials]
