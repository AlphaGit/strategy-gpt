"""Tests for the cheap-critique node."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from strategy_gpt.cheap_critique import cheap_critique
from strategy_gpt.diagnose import (
    Diagnosis,
    RegimePerformance,
    SignalMisfire,
    TradeStats,
)
from strategy_gpt.hypothesis_loop import PriorDecision
from strategy_gpt.markdown_io import Stage1Idea
from strategy_gpt.types import BacktestMetrics, DecisionKind, HypothesisRecord


def _good_idea() -> Stage1Idea:
    return Stage1Idea(
        candidate_name="add_drawdown_guard",
        rationale="Pause new entries when running drawdown exceeds 20%.",
        expected_lift_confidence=0.6,
        expected_side_effects=["fewer entries during drawdowns"],
    )


def _prior(
    *,
    name: str,
    rationale: str,
    kind: DecisionKind,
    decision_id: str = "dec_x",
) -> PriorDecision:
    return PriorDecision(
        decision_id=decision_id,
        kind=kind,
        rationale=rationale,
        evidence=None,
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
        hypothesis=HypothesisRecord(
            id=name,
            name=name,
            target_metric="sharpe",
            falsification={},
            proposed_change={},
            kb_cites=[],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def _empty_diagnosis(*, fired_no_trade: int = 0, used: int = 1) -> Diagnosis:
    return Diagnosis(
        metrics=BacktestMetrics(
            sharpe=0.0,
            sortino=0.0,
            profit_factor=0.0,
            win_ratio=0.0,
            max_drawdown=0.0,
            annualized_return=0.0,
            n_trades=0,
            avg_trade_length_bars=0.0,
        ),
        trade_stats=TradeStats(
            n_total=0,
            n_winners=0,
            n_losers=0,
            n_breakeven=0,
            win_rate=0.0,
            avg_pnl=0.0,
            avg_winner_pnl=0.0,
            avg_loser_pnl=0.0,
            largest_winner_pnl=0.0,
            largest_loser_pnl=0.0,
            total_pnl=0.0,
            total_fees=0.0,
            avg_trade_length_seconds=0.0,
            long_count=0,
            short_count=0,
        ),
        regime_performance=[
            RegimePerformance(
                label="x",
                n_trades=0,
                total_pnl=0.0,
                win_rate=0.0,
                avg_pnl=0.0,
                coverage_bars=0,
            ),
        ],
        signal_misfires=[
            SignalMisfire(
                signal="s",
                fired_count=used + fired_no_trade,
                suppressed_count=0,
                used_count=used,
                fired_no_trade_count=fired_no_trade,
                suppression_reasons={},
            ),
        ],
        exec_log_summary={},
    )


def test_clean_idea_passes() -> None:
    outcome = cheap_critique(
        _good_idea(),
        prior_decisions=[],
        diagnosis=_empty_diagnosis(),
    )
    assert outcome.accept is True
    assert outcome.reason == "ok"


def test_malformed_empty_name_rejected() -> None:
    # markdown_io would reject this at parse time, but the parser
    # accepts trimmed-but-padded names; cheap_critique catches the
    # all-whitespace residual.
    idea = Stage1Idea(
        candidate_name="   ",
        rationale="x",
        expected_lift_confidence=0.5,
        expected_side_effects=[],
    )
    outcome = cheap_critique(idea, prior_decisions=[], diagnosis=None)
    assert outcome.accept is False
    assert outcome.reason == "malformed"


def test_malformed_out_of_range_confidence() -> None:
    idea = Stage1Idea(
        candidate_name="x",
        rationale="y",
        expected_lift_confidence=1.5,  # out of [0, 1]
        expected_side_effects=[],
    )
    outcome = cheap_critique(idea, prior_decisions=[], diagnosis=None)
    assert outcome.accept is False
    assert outcome.reason == "malformed"


def test_duplicate_of_prior_reject_fires() -> None:
    prior = _prior(
        name="add_drawdown_guard",
        rationale="Pause new entries when running drawdown exceeds 20%.",
        kind=DecisionKind.REJECTED,
        decision_id="dec_drawdown",
    )
    outcome = cheap_critique(
        _good_idea(),
        prior_decisions=[prior],
        diagnosis=None,
        similarity_threshold=0.4,
    )
    assert outcome.accept is False
    assert outcome.reason == "duplicate_of_prior_reject"
    assert outcome.matched_decision_id == "dec_drawdown"
    assert "dec_drawdown" in outcome.rationale


def test_duplicate_threshold_not_exceeded_passes() -> None:
    prior = _prior(
        name="totally_different",
        rationale="Unrelated heuristic.",
        kind=DecisionKind.REJECTED,
    )
    outcome = cheap_critique(
        _good_idea(),
        prior_decisions=[prior],
        diagnosis=None,
        similarity_threshold=0.9,
    )
    assert outcome.accept is True


def test_accepted_prior_does_not_trigger_duplicate_check() -> None:
    prior = _prior(
        name="add_drawdown_guard",
        rationale="Pause new entries when running drawdown exceeds 20%.",
        kind=DecisionKind.ACCEPTED,
    )
    outcome = cheap_critique(
        _good_idea(),
        prior_decisions=[prior],
        diagnosis=None,
        similarity_threshold=0.4,
    )
    # An accepted prior is fine; we boost similar ideas elsewhere
    # but cheap_critique only de-dups rejects.
    assert outcome.accept is True


def test_contradicts_diagnosis_when_signal_over_firing() -> None:
    idea = Stage1Idea(
        candidate_name="more_entries",
        rationale="We need to fire more often to catch missed opportunities.",
        expected_lift_confidence=0.5,
        expected_side_effects=[],
    )
    diag = _empty_diagnosis(fired_no_trade=20, used=5)
    outcome = cheap_critique(idea, prior_decisions=[], diagnosis=diag)
    assert outcome.accept is False
    assert outcome.reason == "contradicts_diagnosis"


def test_contradicts_diagnosis_quiet_when_under_firing() -> None:
    idea = Stage1Idea(
        candidate_name="more_entries",
        rationale="We need to fire more often.",
        expected_lift_confidence=0.5,
        expected_side_effects=[],
    )
    # Used > fired_no_trade, so the channel is not over-firing.
    diag = _empty_diagnosis(fired_no_trade=2, used=20)
    outcome = cheap_critique(idea, prior_decisions=[], diagnosis=diag)
    assert outcome.accept is True


def test_violates_prior_accept() -> None:
    prior = _prior(
        name="hedge_leg",
        rationale="treasury hedging accepted",
        kind=DecisionKind.ACCEPTED,
    )
    idea = Stage1Idea(
        candidate_name="remove_hedge_leg",
        rationale="Drop the hedge leg to free capital.",
        expected_lift_confidence=0.4,
        expected_side_effects=[],
    )
    outcome = cheap_critique(idea, prior_decisions=[prior], diagnosis=None)
    assert outcome.accept is False
    assert outcome.reason == "violates_prior_accept"


def test_violates_prior_accept_requires_removal_signal() -> None:
    prior = _prior(name="hedge_leg", rationale="x", kind=DecisionKind.ACCEPTED)
    idea = Stage1Idea(
        candidate_name="tighten_hedge_leg",
        rationale="Make the hedge leg react faster to vol spikes.",
        expected_lift_confidence=0.4,
        expected_side_effects=[],
    )
    outcome = cheap_critique(idea, prior_decisions=[prior], diagnosis=None)
    # "tighten" is not a removal-language match, so the check should not fire.
    assert outcome.accept is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
