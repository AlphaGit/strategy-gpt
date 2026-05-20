"""Tests for the prior-decision-aware kb_filter_node."""

from __future__ import annotations

from datetime import UTC, datetime

from strategy_gpt.hypothesis_loop import (
    HypothesisLoopState,
    KbCitation,
    PriorDecision,
)
from strategy_gpt.kb_query import kb_filter_node
from strategy_gpt.types import DecisionKind, HypothesisRecord


def _hypothesis(kb_cites: list[dict[str, str]]) -> HypothesisRecord:
    return HypothesisRecord(
        id="h",
        name="n",
        target_metric="sharpe",
        falsification=None,
        proposed_change=None,
        kb_cites=kb_cites,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _prior(kind: DecisionKind, cites: list[dict[str, str]]) -> PriorDecision:
    return PriorDecision(
        decision_id="d",
        kind=kind,
        rationale="r",
        evidence=None,
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
        hypothesis=_hypothesis(cites),
    )


def test_recycled_rejected_chunk_dropped() -> None:
    state = HypothesisLoopState(
        kb_cites=[
            KbCitation(source="book_x", locator="p.42", excerpt="A"),
            KbCitation(source="paper_y", locator="sec.3", excerpt="B"),
        ],
        prior_decisions=[
            _prior(DecisionKind.REJECTED, [{"source": "book_x", "locator": "p.42"}]),
        ],
    )
    filtered = kb_filter_node(state)
    assert len(filtered.kb_cites) == 1
    assert filtered.kb_cites[0].source == "paper_y"


def test_recycled_rejected_chunk_suppressed_not_dropped_with_factor() -> None:
    state = HypothesisLoopState(
        kb_cites=[
            KbCitation(source="x", locator="1"),
            KbCitation(source="y", locator="2"),
        ],
        prior_decisions=[
            _prior(DecisionKind.REJECTED, [{"source": "x", "locator": "1"}]),
        ],
    )
    filtered = kb_filter_node(state, suppress_factor=0.5)
    # suppress_factor > 0 keeps the chunk in the result set
    assert len(filtered.kb_cites) == 2


def test_accepted_cite_preserved() -> None:
    state = HypothesisLoopState(
        kb_cites=[KbCitation(source="x", locator="1")],
        prior_decisions=[
            _prior(DecisionKind.ACCEPTED, [{"source": "x", "locator": "1"}]),
        ],
    )
    filtered = kb_filter_node(state)
    assert len(filtered.kb_cites) == 1


def test_accepted_wins_over_rejected() -> None:
    """A chunk cited by BOTH an accepted and a rejected decision keeps
    the boost (accepted) path — the loop has explicit evidence that
    direction is productive."""
    state = HypothesisLoopState(
        kb_cites=[KbCitation(source="x", locator="1")],
        prior_decisions=[
            _prior(DecisionKind.REJECTED, [{"source": "x", "locator": "1"}]),
            _prior(DecisionKind.ACCEPTED, [{"source": "x", "locator": "1"}]),
        ],
    )
    filtered = kb_filter_node(state)
    assert len(filtered.kb_cites) == 1


def test_no_priors_is_noop() -> None:
    state = HypothesisLoopState(
        kb_cites=[KbCitation(source="x", locator="1")],
        prior_decisions=[],
    )
    filtered = kb_filter_node(state)
    assert filtered.kb_cites == state.kb_cites


def test_tolerates_legacy_dict_kb_cites() -> None:
    """Native ledger stores kb_cites as opaque JSON — after a round
    trip they materialize as plain dicts. The filter must consume both
    shapes without an adapter."""
    state = HypothesisLoopState(
        kb_cites=[KbCitation(source="x", locator="1")],
        prior_decisions=[
            PriorDecision(
                decision_id="d",
                kind=DecisionKind.REJECTED,
                rationale="r",
                evidence=None,
                decided_at=datetime(2026, 1, 1, tzinfo=UTC),
                hypothesis=_hypothesis([{"source": "x", "locator": "1", "excerpt": "anything"}]),
            ),
        ],
    )
    filtered = kb_filter_node(state)
    assert filtered.kb_cites == []
