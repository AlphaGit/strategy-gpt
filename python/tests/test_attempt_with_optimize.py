"""Tests for ``tester.attempt_with_optimize``."""

from __future__ import annotations

import pytest

from strategy_gpt.optimizer import ContinuousParam
from strategy_gpt.per_strategy_ledger import (
    AddedParam,
    Falsification,
    FalsificationPrimary,
    GuardConstraint,
    ParamIntent,
)
from strategy_gpt.tester import attempt_with_optimize
from strategy_gpt.types import BacktestMetrics


def _make_evaluator(*, optimal=0.2, lift=0.5, drawdown=0.10, n_trades=120):
    def evaluate(params, fold_idx):
        # synthetic surface: score peaks at vol_lo=optimal
        base = 1.0 + (1.0 - abs(params["vol_lo"] - optimal) * 5) * lift
        return BacktestMetrics(
            sharpe=base + fold_idx * 0.01,
            sortino=base,
            profit_factor=1.5,
            win_ratio=0.55,
            max_drawdown=drawdown,
            annualized_return=0.20,
            n_trades=n_trades,
            avg_trade_length_bars=5.0,
        )

    return evaluate


def _param_intent():
    return ParamIntent(
        added=[AddedParam(name="vol_lo", kind="f64", min=0.0, max=1.0, default=0.2)],
        kept=[],
        removed=[],
    )


def _basic_falsification(*, delta=0.2):
    return Falsification(
        primary=FalsificationPrimary(metric="sharpe", direction="gt", delta_vs_baseline=delta),
        guard_constraints=[
            GuardConstraint(metric="max_drawdown", direction="lte", delta_vs_baseline=0.05)
        ],
    )


def test_returns_per_fold_aggregate_and_best_params() -> None:
    res = attempt_with_optimize(
        strategy_artifact="vxx.so",
        param_intent=_param_intent(),
        falsification=_basic_falsification(),
        folds=3,
        method="sobol",
        trials=16,
        kept_bounds={},
        objective_metric="sharpe",
        evaluate_fold=_make_evaluator(),
        baseline_per_fold_scores=[1.0, 1.05, 1.0],
        baseline_metrics={"max_drawdown": 0.08, "n_trades": 100.0, "avg_trade_length_bars": 5.0},
    )
    assert len(res.per_fold_best_scores) == 3
    assert res.aggregate_score > res.baseline_aggregate_score
    assert "vol_lo" in res.best_params
    # vol_lo near 0.2 (optimum)
    assert abs(res.best_params["vol_lo"] - 0.2) < 0.15
    assert res.falsification_check.classification == "accepted"


def test_removed_param_absent_from_search() -> None:
    pi = ParamIntent(
        added=[AddedParam(name="vol_lo", kind="f64", min=0.0, max=1.0, default=0.2)],
        kept=[],
        removed=["trail_stop_atr_mult"],
    )
    res = attempt_with_optimize(
        strategy_artifact="vxx.so",
        param_intent=pi,
        falsification=_basic_falsification(),
        folds=2,
        method="sobol",
        trials=8,
        kept_bounds={},
        objective_metric="sharpe",
        evaluate_fold=_make_evaluator(),
        baseline_per_fold_scores=[1.0, 1.0],
        baseline_metrics={"max_drawdown": 0.08},
    )
    assert "trail_stop_atr_mult" not in res.best_params


def test_guard_failure_classifies_as_regression() -> None:
    # candidate's drawdown blows past +0.05 vs baseline (0.08 baseline,
    # candidate 0.20 → delta +0.12)
    res = attempt_with_optimize(
        strategy_artifact="vxx.so",
        param_intent=_param_intent(),
        falsification=_basic_falsification(),
        folds=2,
        method="sobol",
        trials=8,
        kept_bounds={},
        objective_metric="sharpe",
        evaluate_fold=_make_evaluator(drawdown=0.20),
        baseline_per_fold_scores=[1.0, 1.0],
        baseline_metrics={"max_drawdown": 0.08},
    )
    assert res.falsification_check.classification == "regression"
    assert any(not g.held for g in res.falsification_check.guard_verdicts)


def test_falsified_when_lift_below_target() -> None:
    res = attempt_with_optimize(
        strategy_artifact="vxx.so",
        param_intent=_param_intent(),
        falsification=_basic_falsification(delta=5.0),  # impossibly large target
        folds=2,
        method="sobol",
        trials=8,
        kept_bounds={},
        objective_metric="sharpe",
        evaluate_fold=_make_evaluator(),
        baseline_per_fold_scores=[1.0, 1.0],
        baseline_metrics={"max_drawdown": 0.08},
    )
    assert res.falsification_check.classification == "falsified"


def test_requires_kept_bounds_for_kept_params() -> None:
    pi = ParamIntent(
        added=[],
        kept=["vol_hi"],
        removed=[],
    )
    with pytest.raises(ValueError, match="kept param"):
        attempt_with_optimize(
            strategy_artifact="x.so",
            param_intent=pi,
            falsification=_basic_falsification(),
            folds=1,
            method="sobol",
            trials=4,
            kept_bounds={},
            objective_metric="sharpe",
            evaluate_fold=_make_evaluator(),
            baseline_per_fold_scores=[1.0],
            baseline_metrics={},
        )


def test_kept_param_uses_supplied_bounds() -> None:
    pi = ParamIntent(
        added=[AddedParam(name="vol_lo", kind="f64", min=0.0, max=1.0, default=0.2)],
        kept=["vol_hi"],
        removed=[],
    )
    res = attempt_with_optimize(
        strategy_artifact="x.so",
        param_intent=pi,
        falsification=_basic_falsification(),
        folds=1,
        method="sobol",
        trials=4,
        kept_bounds={"vol_hi": ContinuousParam(low=0.5, high=0.9)},
        objective_metric="sharpe",
        evaluate_fold=_make_evaluator(),
        baseline_per_fold_scores=[1.0],
        baseline_metrics={"max_drawdown": 0.08},
    )
    assert 0.5 <= res.best_params["vol_hi"] <= 0.9
