"""Hypothesis Loop — generate / critique / rank / select nodes and the
inner ``generate → critique → rank`` iteration loop.

Each node is a pure function over :class:`HypothesisLoopState`. Reasoning
calls go through the :class:`ReasoningClient` protocol so tests can stub
LLM behaviour without touching the network and so the orchestrator can
swap Anthropic / OpenAI back-ends without touching loop logic. The
ranking and selection nodes are deterministic Python — no LLM call —
because score composition and top-K trimming are cheap, replayable, and
better kept off the reasoning budget (`hypothesis-loop::reasoning-model-
usage` only requires `diagnose` and `critique` to be reasoning-capable).

Inner loop (`hypothesis-loop::internal-iteration-loop`):

The loop body is ``generate → critique → rank``. Each iteration
generates candidates to fill the gap to ``target_candidates``, the
critique step accepts or rejects them with a rationale, and rank
re-orders the accumulated accepted set. Termination is checked in this
priority order:

1. ``sufficient_candidates`` — accepted set ≥ ``target_candidates``.
2. ``similarity_saturation`` — every candidate produced this iteration
   resembles a prior rejection above ``similarity_threshold``. Saturation
   is checked second because a saturated iteration may still have
   produced enough new accepts to finish the run.
3. ``budget_exhausted`` — ``iteration`` reached ``iteration_budget``.

The default similarity function is a Jaccard-token overlap over
``name`` + canonical proposed-change JSON. It is intentionally simple
and deterministic so loop replays from the ledger stay stable; KB-aware
embedding similarity is a planned follow-up.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from .diagnose import Diagnosis
from .hypothesis_loop import (
    AcceptedHypothesis,
    HypothesisCandidate,
    HypothesisLoopState,
    KbCitation,
    PriorDecision,
    RejectedHypothesis,
    TerminationReason,
)
from .reasoning import HypothesisLoopConfig, ReasoningModel
from .types import DecisionKind


class CritiqueOutcome(BaseModel):
    """Result of a single critique call.

    ``accept`` decides whether the candidate moves to ``state.accepted``
    or ``state.rejected``. ``rationale`` is the critic's reasoning, kept
    on the decision record for ledger persistence and for the next-loop
    `past-rejected-ideas-inform-future-rejections` context. ``evidence``
    is optional and is attached to accepted records only (rejected
    decisions record ``None`` per the ledger convention)."""

    model_config = ConfigDict(frozen=True)

    accept: bool
    rationale: str
    evidence: Any | None = None


class ReasoningClient(Protocol):
    """LLM-backed reasoning surface consumed by the loop nodes.

    Two methods, one per reasoning-capable node. Both are
    deterministic-or-not at the discretion of the implementation; the
    loop itself does not assume determinism but tests typically use a
    canned-output stub. ``model`` is passed in so the client can route
    to whichever provider matches the operator's configuration without
    re-resolving the env on every call."""

    def generate(
        self,
        *,
        diagnosis: Diagnosis,
        kb_cites: list[KbCitation],
        prior_decisions: list[PriorDecision],
        n: int,
        model: ReasoningModel,
    ) -> list[HypothesisCandidate]:
        """Emit up to ``n`` candidate hypotheses informed by the
        diagnosis and KB citations. Implementations are responsible for
        producing well-formed :class:`HypothesisCandidate` instances —
        falsification criteria, proposed-change shape, lift confidence."""
        ...

    def critique(
        self,
        *,
        candidate: HypothesisCandidate,
        prior_decisions: list[PriorDecision],
        diagnosis: Diagnosis | None,
        model: ReasoningModel,
    ) -> CritiqueOutcome:
        """Attack one candidate and return accept/reject + rationale.
        ``prior_decisions`` lets the critic detect duplicates of
        previously rejected hypotheses; ``diagnosis`` lets it ground its
        objections in the same evidence the generator used."""
        ...


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class GenerateError(RuntimeError):
    """Raised when :func:`generate_node` is called without a diagnosis."""


def generate_node(
    state: HypothesisLoopState,
    *,
    client: ReasoningClient,
    config: HypothesisLoopConfig,
    n: int | None = None,
) -> HypothesisLoopState:
    """Call the reasoning client for new candidates and append them to
    ``state.open``.

    ``n`` defaults to the gap between the accepted set and
    ``config.target_candidates`` (clamped to ≥ 1) so the loop does not
    over-generate once it is close to the target. A diagnosis is
    required — the spec wires ``diagnose`` before the inner loop, and
    asking the LLM to generate without it would defeat the point of the
    workflow.
    """
    if state.diagnosis is None:
        msg = "generate_node requires state.diagnosis (run diagnose_node first)"
        raise GenerateError(msg)
    if n is None:
        gap = config.target_candidates - len(state.accepted)
        n = max(1, gap)
    candidates = client.generate(
        diagnosis=state.diagnosis,
        kb_cites=state.kb_cites,
        prior_decisions=state.prior_decisions,
        n=n,
        model=config.reasoning_model,
    )
    return state.model_copy(update={"open": [*state.open, *candidates]})


# ---------------------------------------------------------------------------
# critique
# ---------------------------------------------------------------------------


def critique_node(
    state: HypothesisLoopState,
    *,
    client: ReasoningClient,
    config: HypothesisLoopConfig,
    now: datetime | None = None,
) -> HypothesisLoopState:
    """Critique every candidate in ``state.open`` and move it to
    ``accepted`` or ``rejected`` based on the client's verdict.

    The open queue is drained — after this node returns, ``state.open``
    is empty. ``now`` is the timestamp stamped on the accepted/rejected
    records (defaults to :func:`datetime.now`). Order is preserved so
    the loop's submission order survives into the ledger.
    """
    stamp = now if now is not None else datetime.now(UTC)
    accepted: list[AcceptedHypothesis] = list(state.accepted)
    rejected: list[RejectedHypothesis] = list(state.rejected)
    for candidate in state.open:
        outcome = client.critique(
            candidate=candidate,
            prior_decisions=state.prior_decisions,
            diagnosis=state.diagnosis,
            model=config.reasoning_model,
        )
        if outcome.accept:
            accepted.append(
                AcceptedHypothesis(
                    candidate=candidate,
                    rationale=outcome.rationale,
                    evidence=outcome.evidence,
                    accepted_at=stamp,
                )
            )
        else:
            rejected.append(
                RejectedHypothesis(
                    candidate=candidate,
                    reason=outcome.rationale,
                    rejected_at=stamp,
                )
            )
    return state.model_copy(
        update={
            "open": [],
            "accepted": accepted,
            "rejected": rejected,
        }
    )


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def _evidence_strength(candidate: HypothesisCandidate) -> float:
    """Reward candidates with citation backing; capped at 1.0."""
    n = len(candidate.kb_cites)
    if n == 0:
        return 0.0
    return min(1.0, 0.4 + 0.2 * n)


@dataclass(frozen=True, slots=True)
class RankWeights:
    """Tunable weights for :func:`rank_score`.

    Defaults match ``hypothesis-loop::simplicity-preferring-rank``:
    lift dominates, evidence second, complexity penalty third, simplicity
    bonus fourth. Operators retune by passing a custom :class:`RankWeights`
    to :func:`rank_node`. The values are stable across replays so the
    ledger's rank order remains deterministic.
    """

    lift: float = 0.55
    evidence: float = 0.25
    complexity_penalty: float = 0.15
    simplicity_bonus: float = 0.05


_DEFAULT_RANK_WEIGHTS = RankWeights()


def _complexity_delta(change: object) -> tuple[int, int]:
    """Compute ``(delta_params, delta_components)`` for a proposed change.

    Spec's continuous complexity differential. Reads either the new
    structured ``files_manifest`` / ``param_intent`` shape (per ADR 0017)
    or the legacy logic-change keys for backwards compatibility. Returns
    ``(0, 0)`` when no structured information is available — the rank
    still works on lift + evidence in that case.
    """
    if not isinstance(change, dict):
        return (0, 0)
    delta_params = 0
    delta_components = 0
    param_intent = change.get("param_intent")
    if isinstance(param_intent, dict):
        added = param_intent.get("added") or []
        removed = param_intent.get("removed") or []
        delta_params = (len(added) if isinstance(added, list) else 0) - (
            len(removed) if isinstance(removed, list) else 0
        )
    files_manifest = change.get("files_manifest")
    deleted_files = change.get("deleted_files")
    if isinstance(files_manifest, dict):
        delta_components = len(files_manifest) - (
            len(deleted_files) if isinstance(deleted_files, list) else 0
        )
    return (delta_params, delta_components)


def rank_score(
    candidate: HypothesisCandidate,
    *,
    weights: RankWeights | None = None,
) -> float:
    """Composite score used by :func:`rank_node`.

    Linear combination of expected lift (LLM-estimated confidence),
    evidence strength (citation count), a complexity penalty proportional
    to net additions, and a simplicity bonus proportional to net removals.
    Stable across replays."""
    w = weights if weights is not None else _DEFAULT_RANK_WEIGHTS
    lift = candidate.estimated_lift_confidence
    evidence = _evidence_strength(candidate)
    dp, dc = _complexity_delta(candidate.proposed_change)
    complexity_delta = dp + dc
    return (
        w.lift * lift
        + w.evidence * evidence
        - w.complexity_penalty * max(0, complexity_delta)
        + w.simplicity_bonus * max(0, -complexity_delta)
    )


def rank_node(
    state: HypothesisLoopState,
    *,
    weights: RankWeights | None = None,
) -> HypothesisLoopState:
    """Reorder ``state.accepted`` by composite score (descending).

    Pure function, deterministic given the same accepted set. Ties keep
    submission order via Python's stable sort."""
    ranked = sorted(
        state.accepted,
        key=lambda a: rank_score(a.candidate, weights=weights),
        reverse=True,
    )
    return state.model_copy(update={"accepted": ranked})


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def select_node(
    state: HypothesisLoopState,
    *,
    k: int,
    termination_reason: TerminationReason | None = None,
) -> HypothesisLoopState:
    """Trim ``state.accepted`` to the top ``k`` entries (already ranked
    by :func:`rank_node`) and finalize ``termination_reason``.

    If ``termination_reason`` is supplied it overrides the state's
    current value; the inner loop uses this to stamp the terminal reason
    once it decides to exit. When called outside the loop and the state
    is already terminated, the existing reason is preserved."""
    if k < 0:
        msg = f"k must be >= 0, got {k}"
        raise ValueError(msg)
    trimmed = list(state.accepted[:k])
    reason = termination_reason if termination_reason is not None else state.termination_reason
    if reason is TerminationReason.RUNNING:
        # Caller invoked select without the inner loop deciding; default
        # to sufficient_candidates if we hit k, otherwise budget_exhausted.
        reason = (
            TerminationReason.SUFFICIENT_CANDIDATES
            if len(trimmed) >= k > 0
            else TerminationReason.BUDGET_EXHAUSTED
        )
    return state.model_copy(
        update={
            "accepted": trimmed,
            "termination_reason": reason,
        }
    )


# ---------------------------------------------------------------------------
# similarity (default implementation)
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text)}


def _candidate_signature(candidate: HypothesisCandidate) -> set[str]:
    """Bag-of-tokens over name + canonical proposed-change JSON.

    Canonical JSON via ``sort_keys=True`` so the signature is stable
    across dict ordering differences. The name is included with a weight
    of 1 (same as any other token); name overlap is a strong signal that
    two candidates are duplicates."""
    payload = json.dumps(candidate.proposed_change, sort_keys=True, default=str)
    return _tokenize(candidate.name) | _tokenize(payload)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


SimilarityFn = Callable[[HypothesisCandidate, list[HypothesisCandidate]], float]


def default_similarity(
    candidate: HypothesisCandidate,
    references: list[HypothesisCandidate],
) -> float:
    """Maximum Jaccard similarity between ``candidate`` and any
    reference. Returns 0 when the reference set is empty."""
    if not references:
        return 0.0
    sig = _candidate_signature(candidate)
    return max(_jaccard(sig, _candidate_signature(ref)) for ref in references)


# ---------------------------------------------------------------------------
# inner loop
# ---------------------------------------------------------------------------


def _prior_rejected_candidates(state: HypothesisLoopState) -> list[HypothesisCandidate]:
    """All rejected candidates the saturation check should compare
    against: in-loop rejects plus prior-run rejected decisions."""
    refs: list[HypothesisCandidate] = [r.candidate for r in state.rejected]
    for pd in state.prior_decisions:
        if pd.kind is not DecisionKind.REJECTED:
            continue
        refs.append(
            HypothesisCandidate(
                name=pd.hypothesis.name,
                target_metric=pd.hypothesis.target_metric,
                falsification=pd.hypothesis.falsification,
                proposed_change=pd.hypothesis.proposed_change,
                kb_cites=[],
                estimated_lift_confidence=0.0,
            )
        )
    return refs


def run_inner_loop(
    state: HypothesisLoopState,
    *,
    client: ReasoningClient,
    config: HypothesisLoopConfig,
    similarity_fn: SimilarityFn | None = None,
    now: datetime | None = None,
) -> HypothesisLoopState:
    """Drive ``generate → critique → rank`` until a termination
    condition is met, then call :func:`select_node` to finalize.

    Returns a state with ``termination_reason`` set to one of
    ``sufficient_candidates`` / ``similarity_saturation`` /
    ``budget_exhausted`` and ``accepted`` trimmed to the top
    ``config.target_candidates`` entries. The state's ``iteration``
    counter reflects the number of inner-loop passes executed.
    """
    if state.diagnosis is None:
        msg = "run_inner_loop requires state.diagnosis"
        raise GenerateError(msg)
    sim = similarity_fn if similarity_fn is not None else default_similarity
    current = state
    reason: TerminationReason | None = None
    while True:
        # Snapshot prior rejections before critique runs so saturation
        # compares the new generation against pre-existing duplicates,
        # not against itself.
        refs_before = _prior_rejected_candidates(current)
        current = generate_node(current, client=client, config=config)
        new_candidates = list(current.open)
        current = critique_node(current, client=client, config=config, now=now)
        current = rank_node(current)
        current = current.model_copy(update={"iteration": current.iteration + 1})

        if len(current.accepted) >= config.target_candidates:
            reason = TerminationReason.SUFFICIENT_CANDIDATES
            break

        if new_candidates and all(
            sim(c, refs_before) >= config.similarity_threshold for c in new_candidates
        ):
            reason = TerminationReason.SIMILARITY_SATURATION
            break

        if current.iteration >= config.iteration_budget:
            reason = TerminationReason.BUDGET_EXHAUSTED
            break

    return select_node(current, k=config.target_candidates, termination_reason=reason)


__all__ = [
    "CritiqueOutcome",
    "GenerateError",
    "RankWeights",
    "ReasoningClient",
    "SimilarityFn",
    "critique_node",
    "default_similarity",
    "generate_node",
    "rank_node",
    "rank_score",
    "run_inner_loop",
    "select_node",
]
