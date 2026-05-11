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
    PersistedDecision,
    PriorDecision,
    RejectedHypothesis,
    TerminationReason,
    accepted_to_decision_record,
    bootstrap_state_from_ledger,
    candidate_to_hypothesis_record,
    parse_prior_decisions,
    persist_decisions,
    rejected_to_decision_record,
)
from strategy_gpt.types import DecisionKind, DecisionRecord, HypothesisRecord


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


class _FakeLedger:
    """In-memory stand-in for `strategy_gpt.ledger.Ledger.recent_decisions`."""

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.last_limit: int | None = None

    def recent_decisions(self, limit: int) -> str:
        self.last_limit = limit
        return self._payload


def _hypothesis_record(hid: str = "h-1") -> HypothesisRecord:
    return HypothesisRecord(
        id=hid,
        name="lower_vol_lo",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change={"param": "vol_lo", "from": 10, "to": 5},
        kb_cites=[{"source": "Hull 11e", "locator": "p. 412"}],
        created_at=datetime(2024, 5, 30, tzinfo=UTC),
    )


def test_parse_prior_decisions_round_trips() -> None:
    rec = _hypothesis_record()
    pd = PriorDecision(
        decision_id="d-1",
        kind=DecisionKind.REJECTED,
        rationale="redundant",
        evidence={"prior_run": "abc"},
        decided_at=datetime(2024, 6, 1, tzinfo=UTC),
        hypothesis=rec,
    )
    payload = json.dumps([json.loads(pd.model_dump_json())])
    parsed = parse_prior_decisions(payload)
    assert parsed == [pd]


def test_parse_prior_decisions_rejects_non_array() -> None:
    with pytest.raises(ValueError, match="array"):
        parse_prior_decisions("{}")


def test_bootstrap_state_loads_prior_decisions_from_ledger() -> None:
    rec = _hypothesis_record("h-7")
    pd = PriorDecision(
        decision_id="d-7",
        kind=DecisionKind.ACCEPTED,
        rationale="passes",
        evidence={"sharpe_lift": 0.4},
        decided_at=datetime(2024, 6, 3, tzinfo=UTC),
        hypothesis=rec,
    )
    ledger = _FakeLedger(json.dumps([json.loads(pd.model_dump_json())]))
    state = bootstrap_state_from_ledger(ledger, limit=10)
    assert ledger.last_limit == 10
    assert state.prior_decisions == [pd]
    # Bootstrap does not perturb other state fields.
    assert state.iteration == 0
    assert state.open == []
    assert state.accepted == []
    assert state.rejected == []
    assert state.termination_reason is TerminationReason.RUNNING


def test_bootstrap_state_preserves_supplied_state_fields() -> None:
    ledger = _FakeLedger("[]")
    seed = HypothesisLoopState(iteration=3, kb_cites=[KbCitation(source="x", locator="y")])
    state = bootstrap_state_from_ledger(ledger, state=seed)
    assert state.iteration == 3
    assert state.kb_cites == seed.kb_cites
    assert state.prior_decisions == []


class _RecordingLedger:
    """In-memory ledger that records write order for `persist_decisions`."""

    def __init__(self) -> None:
        self.hypotheses: list[HypothesisRecord] = []
        self.decisions: list[DecisionRecord] = []

    def recent_decisions(self, limit: int) -> str:  # pragma: no cover - unused
        _ = limit
        return "[]"

    def record_hypothesis(self, record: HypothesisRecord) -> None:
        self.hypotheses.append(record)

    def record_decision(self, record: DecisionRecord) -> None:
        self.decisions.append(record)


def test_persist_decisions_writes_accepted_then_rejected() -> None:
    cand = _candidate()
    state = HypothesisLoopState(
        accepted=[
            AcceptedHypothesis(
                candidate=cand,
                rationale="passes critique",
                evidence={"sharpe_lift": 0.3},
                accepted_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        ],
        rejected=[
            RejectedHypothesis(
                candidate=cand,
                reason="redundant",
                rejected_at=datetime(2024, 6, 2, tzinfo=UTC),
            )
        ],
    )
    ledger = _RecordingLedger()
    persisted = persist_decisions(ledger, state, now=datetime(2024, 6, 3, tzinfo=UTC))
    assert len(persisted) == 2
    assert [p.kind for p in persisted] == [
        DecisionKind.ACCEPTED,
        DecisionKind.REJECTED,
    ]
    # Each row got its own hypothesis even though the candidate is the
    # same — the ledger is append-only so we record one hypothesis per
    # decision and let the join surface them.
    assert {h.id for h in ledger.hypotheses} == {p.hypothesis_id for p in persisted}
    assert {d.id for d in ledger.decisions} == {p.decision_id for p in persisted}
    # Created-at stamp from `now` flows onto every hypothesis row.
    for h in ledger.hypotheses:
        assert h.created_at == datetime(2024, 6, 3, tzinfo=UTC)
    # Decisions carry the accepted/rejected timestamps from their entries.
    accepted_dec = next(d for d in ledger.decisions if d.kind is DecisionKind.ACCEPTED)
    rejected_dec = next(d for d in ledger.decisions if d.kind is DecisionKind.REJECTED)
    assert accepted_dec.decided_at == datetime(2024, 6, 1, tzinfo=UTC)
    assert accepted_dec.rationale == "passes critique"
    assert accepted_dec.evidence == {"sharpe_lift": 0.3}
    assert rejected_dec.decided_at == datetime(2024, 6, 2, tzinfo=UTC)
    assert rejected_dec.rationale == "redundant"
    assert rejected_dec.evidence is None
    # KB citations flow through to the hypothesis rows.
    for h in ledger.hypotheses:
        assert h.kb_cites == [{"source": "Hull 11e", "locator": "p. 412", "excerpt": None}]


def test_persist_decisions_empty_state_writes_nothing() -> None:
    ledger = _RecordingLedger()
    persisted = persist_decisions(ledger, HypothesisLoopState())
    assert persisted == []
    assert ledger.hypotheses == []
    assert ledger.decisions == []


def test_persist_decisions_assigns_unique_ids() -> None:
    cand = _candidate()
    accepted = [
        AcceptedHypothesis(
            candidate=cand,
            rationale=f"r{i}",
            evidence=None,
            accepted_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        for i in range(3)
    ]
    state = HypothesisLoopState(accepted=accepted)
    persisted = persist_decisions(_RecordingLedger(), state)
    assert isinstance(persisted[0], PersistedDecision)
    ids = [p.hypothesis_id for p in persisted] + [p.decision_id for p in persisted]
    assert len(set(ids)) == len(ids)
