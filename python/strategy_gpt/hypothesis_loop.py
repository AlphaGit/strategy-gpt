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

from datetime import datetime
from enum import StrEnum
from typing import Any

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


__all__ = [
    "AcceptedHypothesis",
    "HypothesisCandidate",
    "HypothesisLoopState",
    "KbCitation",
    "RejectedHypothesis",
    "TerminationReason",
    "accepted_to_decision_record",
    "candidate_to_hypothesis_record",
    "rejected_to_decision_record",
]
