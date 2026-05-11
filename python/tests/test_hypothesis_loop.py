"""State-schema round-trip tests for the hypothesis loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from strategy_gpt.hypothesis_loop import (
    AcceptedHypothesis,
    HypothesisCandidate,
    HypothesisLoopState,
    KbCitation,
    RejectedHypothesis,
    TerminationReason,
    accepted_to_decision_record,
    candidate_to_hypothesis_record,
    rejected_to_decision_record,
)
from strategy_gpt.types import DecisionKind


def _candidate() -> HypothesisCandidate:
    return HypothesisCandidate(
        name="lower_vol_lo",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change={"param": "vol_lo", "from": 10, "to": 5},
        kb_cites=[KbCitation(source="Hull 11e", locator="p. 412")],
        estimated_lift_confidence=0.6,
    )


def test_default_state_is_running() -> None:
    s = HypothesisLoopState()
    assert s.iteration == 0
    assert s.termination_reason is TerminationReason.RUNNING
    assert not s.is_terminated()
    assert s.open == []
    assert s.accepted == []
    assert s.rejected == []


def test_state_round_trips_through_json() -> None:
    c = _candidate()
    s = HypothesisLoopState(
        iteration=2,
        open=[c],
        accepted=[
            AcceptedHypothesis(
                candidate=c,
                rationale="passes critique",
                evidence={"sharpe_lift": 0.3},
                accepted_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        ],
        rejected=[
            RejectedHypothesis(
                candidate=c,
                reason="similar to prior reject",
                rejected_at=datetime(2024, 6, 2, tzinfo=UTC),
            )
        ],
        kb_cites=[KbCitation(source="Hull 11e", locator="p. 412")],
        termination_reason=TerminationReason.SUFFICIENT_CANDIDATES,
    )
    payload = json.loads(s.model_dump_json())
    assert payload["iteration"] == 2
    assert payload["termination_reason"] == "sufficient_candidates"
    assert payload["open"][0]["kb_cites"][0]["source"] == "Hull 11e"
    restored = HypothesisLoopState.model_validate(payload)
    assert restored == s
    assert restored.is_terminated()


def test_candidate_validates_confidence_bounds() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        HypothesisCandidate(
            name="bad",
            target_metric="sharpe",
            falsification={},
            proposed_change={},
            estimated_lift_confidence=1.5,
        )


def test_candidate_to_hypothesis_record_carries_cites() -> None:
    c = _candidate()
    rec = candidate_to_hypothesis_record(
        c,
        hypothesis_id="h-1",
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    assert rec.id == "h-1"
    assert rec.name == c.name
    assert rec.target_metric == "sharpe"
    assert isinstance(rec.kb_cites, list)
    assert rec.kb_cites[0]["source"] == "Hull 11e"


def test_accepted_and_rejected_decision_records() -> None:
    c = _candidate()
    accepted = AcceptedHypothesis(
        candidate=c,
        rationale="passes",
        evidence={"score": 1.7},
        accepted_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    rejected = RejectedHypothesis(
        candidate=c,
        reason="redundant",
        rejected_at=datetime(2024, 6, 2, tzinfo=UTC),
    )
    a = accepted_to_decision_record(accepted, decision_id="d-1", hypothesis_id="h-1")
    r = rejected_to_decision_record(rejected, decision_id="d-2", hypothesis_id="h-1")
    assert a.kind is DecisionKind.ACCEPTED
    assert a.rationale == "passes"
    assert a.evidence == {"score": 1.7}
    assert r.kind is DecisionKind.REJECTED
    assert r.rationale == "redundant"
    assert r.evidence is None


def test_termination_reason_serializes_snake_case() -> None:
    for reason in TerminationReason:
        # StrEnum values are the JSON-serialized form.
        assert reason.value == reason.value.lower()
        assert " " not in reason.value
