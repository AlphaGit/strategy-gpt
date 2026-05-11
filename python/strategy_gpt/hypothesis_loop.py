"""Hypothesis Loop state schema.

LangGraph state for the loop's `diagnose → kb_query → generate → critique
→ rank → select` workflow. This module defines the pydantic types that
travel between nodes; node implementations land with their respective
tasks (9.2 - 9.7). The state itself is a frozen pydantic model so the
graph operates on explicit immutable transitions, satisfying
`hypothesis-loop::langgraph-workflow-with-explicit-nodes`.

Why types live here rather than in :mod:`strategy_gpt.types`:

- `types` mirrors the Rust-side serde records (cross-FFI types). The
  hypothesis loop is a Python-only construct; coupling these into the
  same module would imply a Rust mirror that does not exist.
- The transient loop state never crosses the PyO3 boundary. Accepted /
  rejected entries are projected to :class:`HypothesisRecord` and
  :class:`DecisionRecord` in :mod:`strategy_gpt.types` when persisted to
  the ledger.

Termination semantics (`hypothesis-loop::internal-iteration-loop`):

- ``sufficient_candidates`` — at least K accepted in the open set.
- ``budget_exhausted`` — iteration counter reached the configured cap.
- ``similarity_saturation`` — newly generated candidates resemble prior
  rejected items above a configured threshold.
- ``running`` — sentinel for in-progress state; serialized only when
  callers persist intermediate snapshots.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .types import DecisionKind, DecisionRecord, HypothesisRecord


class KbCitation(BaseModel):
    """Source-provenance entry attached to a hypothesis.

    Mirrors the `hypothesis-loop::knowledge-base-queries-with-citation-
    capture` requirement: every retrieval result carries enough
    provenance for a human to verify the claim. ``locator`` is free-form
    (page number, section heading, paragraph anchor) so different source
    types (book, paper, blog post) can all be cited uniformly.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    locator: str
    excerpt: str | None = None


class HypothesisCandidate(BaseModel):
    """A single candidate hypothesis carried through the loop.

    Fields satisfy `hypothesis-loop::hypothesis-output-schema`: name,
    target metric, falsification criterion, proposed change, citations,
    and an estimated lift confidence in [0, 1].

    ``falsification`` and ``proposed_change`` are free-form JSON values
    because their shape varies by hypothesis kind (parameter diff vs.
    new strategy source). The Tester (phase 10) interprets them.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    target_metric: str
    falsification: Any
    proposed_change: Any
    kb_cites: list[KbCitation] = Field(default_factory=list)
    estimated_lift_confidence: float = Field(ge=0.0, le=1.0)


class AcceptedHypothesis(BaseModel):
    """A candidate that passed `critique`."""

    model_config = ConfigDict(frozen=True)

    candidate: HypothesisCandidate
    rationale: str
    evidence: Any
    accepted_at: datetime


class RejectedHypothesis(BaseModel):
    """A candidate that failed `critique`. ``reason`` is the rejection
    rationale recorded so future iterations can reason about prior
    failures (`hypothesis-loop::past-rejected-ideas-inform-future-
    rejections`)."""

    model_config = ConfigDict(frozen=True)

    candidate: HypothesisCandidate
    reason: str
    rejected_at: datetime


class TerminationReason(StrEnum):
    """Why the inner `generate → critique → rank` loop exited."""

    RUNNING = "running"
    SUFFICIENT_CANDIDATES = "sufficient_candidates"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SIMILARITY_SATURATION = "similarity_saturation"


class PriorDecision(BaseModel):
    """A decision recorded by an earlier hypothesis-loop run.

    Mirrors the Rust `ledger::RecentDecision` join shape so the
    orchestrator can load past accepted/rejected hypotheses into the
    current workflow's context. Consumed by ``critique`` to honour
    `hypothesis-loop::past-rejected-ideas-inform-future-rejections`.
    """

    model_config = ConfigDict(frozen=True)

    decision_id: str
    kind: DecisionKind
    rationale: str
    evidence: Any
    decided_at: datetime
    hypothesis: HypothesisRecord


class HypothesisLoopState(BaseModel):
    """LangGraph state passed between hypothesis-loop nodes.

    The graph mutates this object node-by-node:

    - ``diagnose`` populates the diagnostic summary that the prompt for
      ``generate`` consumes (kept as an opaque payload in
      ``diagnosis`` until phase 9.2 lands).
    - ``kb_query`` extends ``kb_cites`` and rewrites ``open`` candidates
      with retrieved citations.
    - ``generate`` appends new candidates to ``open``.
    - ``critique`` moves entries from ``open`` to ``accepted`` /
      ``rejected``.
    - ``rank`` reorders ``accepted``.
    - ``select`` finalizes the K-best slice and sets
      ``termination_reason`` to one of the terminal variants.

    All collections are concrete lists so LangGraph's reducer can
    accumulate across iterations without surprises.
    """

    model_config = ConfigDict(frozen=False)

    iteration: int = 0
    open: list[HypothesisCandidate] = Field(default_factory=list)
    accepted: list[AcceptedHypothesis] = Field(default_factory=list)
    rejected: list[RejectedHypothesis] = Field(default_factory=list)
    kb_cites: list[KbCitation] = Field(default_factory=list)
    prior_decisions: list[PriorDecision] = Field(default_factory=list)
    diagnosis: Any = None
    termination_reason: TerminationReason = TerminationReason.RUNNING

    def is_terminated(self) -> bool:
        """``True`` once a terminal `TerminationReason` is set."""
        return self.termination_reason is not TerminationReason.RUNNING


def candidate_to_hypothesis_record(
    candidate: HypothesisCandidate,
    *,
    hypothesis_id: str,
    created_at: datetime,
) -> HypothesisRecord:
    """Project a transient loop candidate to a ledger
    :class:`HypothesisRecord` (cross-FFI persistence shape). The ledger
    side stores ``falsification``, ``proposed_change``, and ``kb_cites``
    as opaque JSON; we serialize the pydantic fields directly."""
    return HypothesisRecord(
        id=hypothesis_id,
        name=candidate.name,
        target_metric=candidate.target_metric,
        falsification=candidate.falsification,
        proposed_change=candidate.proposed_change,
        kb_cites=[c.model_dump(mode="json") for c in candidate.kb_cites],
        created_at=created_at,
    )


def accepted_to_decision_record(
    entry: AcceptedHypothesis,
    *,
    decision_id: str,
    hypothesis_id: str,
) -> DecisionRecord:
    """Project an :class:`AcceptedHypothesis` to a ledger
    :class:`DecisionRecord` with kind ``accepted``."""
    return DecisionRecord(
        id=decision_id,
        hypothesis_id=hypothesis_id,
        kind=DecisionKind.ACCEPTED,
        rationale=entry.rationale,
        evidence=entry.evidence,
        decided_at=entry.accepted_at,
    )


def rejected_to_decision_record(
    entry: RejectedHypothesis,
    *,
    decision_id: str,
    hypothesis_id: str,
) -> DecisionRecord:
    """Project a :class:`RejectedHypothesis` to a ledger
    :class:`DecisionRecord` with kind ``rejected``."""
    return DecisionRecord(
        id=decision_id,
        hypothesis_id=hypothesis_id,
        kind=DecisionKind.REJECTED,
        rationale=entry.reason,
        evidence=None,
        decided_at=entry.rejected_at,
    )


def parse_prior_decisions(json_array: str) -> list[PriorDecision]:
    """Parse the JSON array returned by ``Ledger.recent_decisions``.

    The native side returns a string instead of a pre-parsed list because
    the join shape lived only on the Rust side. Hypothesis-loop bootstrap
    needs typed records, so this helper deserializes once and validates
    against :class:`PriorDecision`.
    """
    raw = json.loads(json_array)
    if not isinstance(raw, list):
        msg = "expected a JSON array of recent decisions"
        raise ValueError(msg)
    return [PriorDecision.model_validate(item) for item in raw]


class _LedgerLike(Protocol):
    """Subset of :class:`strategy_gpt.ledger.Ledger` used by the loop.

    Declared structurally to avoid a hard import (the ledger module
    already imports types from this module — the cycle would be
    unwelcome) and to keep tests free of a native-extension dependency.
    """

    def recent_decisions(self, limit: int) -> str: ...

    def record_hypothesis(self, record: HypothesisRecord) -> None: ...

    def record_decision(self, record: DecisionRecord) -> None: ...


class PersistedDecision(BaseModel):
    """ID pair returned for each entry persisted by :func:`persist_decisions`."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    decision_id: str
    kind: DecisionKind


def _new_id() -> str:
    return uuid.uuid4().hex


def persist_decisions(
    ledger: _LedgerLike,
    state: HypothesisLoopState,
    *,
    now: datetime | None = None,
) -> list[PersistedDecision]:
    """Append every ``accepted`` and ``rejected`` entry in ``state`` to the
    experiment ledger.

    Each entry is persisted in two records: a :class:`HypothesisRecord`
    (carrying name, target metric, falsification criterion, proposed
    change, and KB citations — `hypothesis-loop::decision-log-
    persistence`) followed by a :class:`DecisionRecord` (carrying the
    rationale and timestamp). Fresh UUID hexes are assigned for both
    rows; the IDs are returned in submission order so callers can
    correlate. The ledger is append-only, so repeat calls record
    duplicates — idempotency is the caller's responsibility.

    Submission order: accepted entries before rejected, matching the
    state's own ordering. ``now`` is the timestamp stamped onto each
    hypothesis row (defaults to :func:`datetime.now`); decisions use
    the entry's own ``accepted_at`` / ``rejected_at``.
    """
    stamp = now if now is not None else datetime.now(UTC)
    persisted: list[PersistedDecision] = []

    for accepted in state.accepted:
        hid = _new_id()
        did = _new_id()
        ledger.record_hypothesis(
            candidate_to_hypothesis_record(accepted.candidate, hypothesis_id=hid, created_at=stamp)
        )
        ledger.record_decision(
            accepted_to_decision_record(accepted, decision_id=did, hypothesis_id=hid)
        )
        persisted.append(
            PersistedDecision(hypothesis_id=hid, decision_id=did, kind=DecisionKind.ACCEPTED)
        )

    for rejected in state.rejected:
        hid = _new_id()
        did = _new_id()
        ledger.record_hypothesis(
            candidate_to_hypothesis_record(rejected.candidate, hypothesis_id=hid, created_at=stamp)
        )
        ledger.record_decision(
            rejected_to_decision_record(rejected, decision_id=did, hypothesis_id=hid)
        )
        persisted.append(
            PersistedDecision(hypothesis_id=hid, decision_id=did, kind=DecisionKind.REJECTED)
        )

    return persisted


def bootstrap_state_from_ledger(
    ledger: _LedgerLike,
    *,
    limit: int = 50,
    state: HypothesisLoopState | None = None,
) -> HypothesisLoopState:
    """Initialise a :class:`HypothesisLoopState` with recent ledger
    decisions attached as ``prior_decisions``.

    Calls ``ledger.recent_decisions(limit)``, parses the JSON, and
    returns a new state with ``prior_decisions`` populated. Existing
    ``state`` fields are preserved when one is supplied; the typical
    caller passes no state and uses the bootstrap output as the
    workflow's entry state.
    """
    json_array: str = ledger.recent_decisions(limit)
    decisions = parse_prior_decisions(json_array)
    base = state if state is not None else HypothesisLoopState()
    return base.model_copy(update={"prior_decisions": decisions})


__all__ = [
    "AcceptedHypothesis",
    "HypothesisCandidate",
    "HypothesisLoopState",
    "KbCitation",
    "PersistedDecision",
    "PriorDecision",
    "RejectedHypothesis",
    "TerminationReason",
    "accepted_to_decision_record",
    "bootstrap_state_from_ledger",
    "candidate_to_hypothesis_record",
    "parse_prior_decisions",
    "persist_decisions",
    "rejected_to_decision_record",
]
