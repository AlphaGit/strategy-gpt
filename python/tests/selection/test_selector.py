"""Unit tests for the selection orchestrator."""

from __future__ import annotations

from strategy_gpt.selection.cscv import PboKnobs
from strategy_gpt.selection.selector import (
    SelectionCandidate,
    SelectionKnobs,
    SelectionStatus,
    run_selection,
)
from strategy_gpt.selection.sensitivity import TrialPoint


def _candidate(  # noqa: PLR0913 — test builder; kwargs keep call sites readable.
    trial_id: int,
    *,
    params: dict[str, float] | None = None,
    sharpe: float = 1.5,
    score: float = 1.5,
    per_fold: list[float] | None = None,
    accepted: bool = True,
    n_trades: int = 200,
) -> SelectionCandidate:
    return SelectionCandidate(
        trial_id=trial_id,
        params=params or {"x": float(trial_id)},
        aggregate_score=score,
        aggregate_metrics={"sharpe": sharpe, "n_trades": float(n_trades)},
        per_fold_oos_primary=per_fold or [sharpe] * 8,
        trade_count=n_trades,
        accepted=accepted,
    )


def test_accepts_low_pbo_signal_rich() -> None:
    """Strong, consistent signal across folds → accepted."""
    candidates = [
        _candidate(0, sharpe=2.0, score=2.0, per_fold=[2.0] * 8),
        _candidate(1, sharpe=1.0, score=1.0, per_fold=[1.0] * 8),
        _candidate(2, sharpe=0.5, score=0.5, per_fold=[0.5] * 8),
    ]
    history = [TrialPoint(params=c.params, score=c.aggregate_score) for c in candidates]
    out = run_selection(candidates, history, SelectionKnobs())
    assert out.status == SelectionStatus.ACCEPTED
    assert out.best_trial_id == 0
    assert out.pbo.pbo == 0.0


def test_rejects_high_pbo_without_force() -> None:
    """Perfect-overfit pattern → PBO ≈ 1 → rejected_pbo unless force."""
    candidates = []
    n = 8
    for i in range(n):
        per_fold = [-1.0] * n
        per_fold[i] = 10.0  # IS spike on fold i; OOS poor everywhere else.
        candidates.append(_candidate(i, score=0.5, per_fold=per_fold))
    history = [TrialPoint(params=c.params, score=c.aggregate_score) for c in candidates]
    out = run_selection(candidates, history, SelectionKnobs())
    assert out.status == SelectionStatus.REJECTED_PBO
    assert out.best_trial_id is None
    assert out.would_have_picked_trial_id is not None
    assert "PBO=" in out.reason


def test_force_override_accepts_despite_pbo() -> None:
    candidates = []
    n = 8
    for i in range(n):
        per_fold = [-1.0] * n
        per_fold[i] = 10.0
        candidates.append(_candidate(i, score=0.5, per_fold=per_fold))
    history = [TrialPoint(params=c.params, score=c.aggregate_score) for c in candidates]
    out = run_selection(candidates, history, SelectionKnobs(), force=True)
    assert out.status == SelectionStatus.ACCEPTED
    assert out.best_trial_id is not None
    assert out.force_override is True
    # PBO is still computed and recorded even with --force.
    assert out.pbo.pbo > 0.5


def test_threshold_override_flips_decision() -> None:
    """Raise the threshold above the observed PBO and the run is accepted."""
    candidates = []
    n = 8
    for i in range(n):
        per_fold = [-1.0] * n
        per_fold[i] = 10.0
        candidates.append(_candidate(i, score=0.5, per_fold=per_fold))
    history = [TrialPoint(params=c.params, score=c.aggregate_score) for c in candidates]
    rejected = run_selection(candidates, history, SelectionKnobs())
    accepted = run_selection(candidates, history, SelectionKnobs(), pbo_threshold_override=1.0)
    assert rejected.status == SelectionStatus.REJECTED_PBO
    assert accepted.status == SelectionStatus.ACCEPTED
    assert accepted.pbo_threshold == 1.0


def test_robust_objective_can_change_winner() -> None:
    """On a knife-edge surface, --robust-objective selects a different candidate than DSR."""
    knife = _candidate(
        0,
        params={"x": 0.5},
        sharpe=3.0,
        score=3.0,
        per_fold=[3.0] * 8,
        n_trades=200,
    )
    plateau = _candidate(
        1,
        params={"x": 0.9},
        sharpe=2.0,
        score=2.0,
        per_fold=[2.0] * 8,
        n_trades=200,
    )
    candidates = [knife, plateau]
    # Add a dense low-score neighborhood around the knife-edge point and a
    # high-and-stable neighborhood around the plateau point.
    history: list[TrialPoint] = []
    for i in range(20):
        x = 0.5 + (i - 10) * 0.005  # tight cluster around the knife.
        history.append(TrialPoint(params={"x": x}, score=-1.0))
    for i in range(20):
        x = 0.9 + (i - 10) * 0.005  # tight cluster around the plateau.
        history.append(TrialPoint(params={"x": x}, score=2.0))

    knobs = SelectionKnobs(pbo=PboKnobs(enabled=False))
    out_dsr = run_selection(candidates, history, knobs)
    out_robust = run_selection(candidates, history, knobs, robust_objective=True)
    assert out_dsr.status == SelectionStatus.ACCEPTED
    assert out_robust.status == SelectionStatus.ACCEPTED
    assert out_dsr.best_trial_id == 0  # raw DSR favors the knife-edge
    assert out_robust.best_trial_id == 1  # robust score favors the plateau


def test_empty_candidates_yields_rejected_constraint() -> None:
    out = run_selection([], [], SelectionKnobs())
    assert out.status == SelectionStatus.REJECTED_CONSTRAINT
    assert out.best_trial_id is None


def test_all_unaccepted_yields_rejected_constraint() -> None:
    candidates = [_candidate(0, accepted=False, per_fold=[0.0] * 8)]
    out = run_selection(candidates, [], SelectionKnobs(pbo=PboKnobs(enabled=False)))
    assert out.status == SelectionStatus.REJECTED_CONSTRAINT


def test_methodology_recorded() -> None:
    out = run_selection(
        [_candidate(0, per_fold=[1.0] * 8)],
        [],
        SelectionKnobs(),
    )
    assert "pbo" in out.methodology
    assert "dsr" in out.methodology
    assert "sensitivity" in out.methodology
