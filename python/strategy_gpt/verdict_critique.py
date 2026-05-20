"""Verdict-critique node — LLM review after the mechanical gate.

Implements ``hypothesis-loop::verdict-critique-with-no-gate-override``.
Invoked only when the mechanical gate accepted the candidate; never
runs on a gate rejection. The LLM is asked to review:

- the measured aggregate / per-fold metrics vs the LLM's own
  stage-1 ``expected_lift_confidence`` and ``expected_side_effects``,
- side-effect deltas flagged by :func:`tester.attempt_with_optimize`,
- the rationale-vs-result coherence (gain came from a regime the
  rationale predicted, etc.),
- the complexity-cost asymmetry (small gain at large delta_params /
  delta_components).

The node MUST NOT override or reverse a mechanical-gate rejection. If
this node fires at all, the gate already accepted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .markdown_io import Stage1Idea
from .mechanical_gate import MechanicalGateOutcome
from .reasoning import ReasoningModel
from .reject_taxonomy import RejectKind, verdict_rationale


@dataclass(frozen=True, slots=True)
class VerdictCritiqueInput:
    """Structured payload handed to the verdict-critique reasoning call.

    Bundles every piece of evidence the LLM needs to render an
    accept/reject verdict. Free-form prose is kept off the input — the
    LLM is asked to read structured fields and return a structured
    verdict so the orchestration layer stays predictable.
    """

    candidate_name: str
    stage1_idea: Stage1Idea
    aggregate_score: float
    baseline_aggregate_score: float
    per_fold_scores: list[float]
    baseline_per_fold_scores: list[float]
    side_effect_flags: list[str]
    candidate_metrics_avg: dict[str, float] = field(default_factory=dict)
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    mechanical_gate: MechanicalGateOutcome | None = None
    delta_params: int = 0
    delta_components: int = 0
    expected_side_effect_envelope: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerdictCritiqueDecision:
    """Result returned by a :class:`VerdictCritiqueClient` call.

    ``accept`` is the LLM's recommendation. ``reasons`` enumerates which
    rejection categories fired (side_effect_envelope /
    rationale_result_mismatch / complexity_cost / overfit_signature);
    the orchestrator persists this list with the
    :class:`DecisionRecord.evidence`.
    """

    accept: bool
    reasons: list[str]
    rationale: str
    detail: dict[str, Any]


class VerdictCritiqueClient(Protocol):
    """LLM client surface for the verdict-critique node.

    A thin protocol so the orchestrator can swap in the real
    Anthropic / OpenAI client, a recorded-replay client for byte-identical
    smoke runs, or a deterministic stub for unit tests.
    """

    def critique_verdict(
        self,
        payload: VerdictCritiqueInput,
        *,
        model: ReasoningModel,
    ) -> VerdictCritiqueDecision: ...


# ---------------------------------------------------------------------------
# Heuristic helpers (used by deterministic critics and as defaults that
# the LLM may consult inside its prompt)
# ---------------------------------------------------------------------------


def side_effect_envelope_breach(
    flags: Sequence[str],
    envelope: dict[str, tuple[float, float]],
) -> list[str]:
    """Return flag tags that fall outside the LLM-stated envelope.

    The envelope is keyed by metric → ``(low_ratio, high_ratio)``. A
    flag like ``"n_trades_up_2.50x"`` is breached when the parsed ratio
    is outside its envelope band; flags without a recognized envelope
    entry pass through silently.
    """
    breaches: list[str] = []
    for flag in flags:
        for metric, (lo, hi) in envelope.items():
            if not flag.startswith(metric):
                continue
            ratio = _ratio_from_flag(flag, metric)
            if ratio is None:
                continue
            if ratio < lo or ratio > hi:
                breaches.append(flag)
            break
    return breaches


def _ratio_from_flag(flag: str, metric: str) -> float | None:
    """Recover the ratio embedded in a side-effect flag.

    Flag formats produced by :func:`tester._side_effect_flags`:
    ``"{metric}_up_{ratio}x"`` or ``"{metric}_down_{ratio}x"``.
    """
    rest = flag[len(metric) :]
    if rest.startswith("_up_") and rest.endswith("x"):
        try:
            return float(rest[len("_up_") : -1])
        except ValueError:
            return None
    if rest.startswith("_down_") and rest.endswith("x"):
        try:
            return float(rest[len("_down_") : -1])
        except ValueError:
            return None
    return None


def complexity_cost_asymmetry(
    *,
    aggregate_score: float,
    baseline_aggregate_score: float,
    delta_params: int,
    delta_components: int,
    lift_per_addition_floor: float = 0.05,
) -> bool:
    """Detect a small-gain / large-addition asymmetry.

    Returns ``True`` when the candidate adds parameters or components
    but the per-addition lift is under the configured floor. This is a
    deterministic check the verdict-critique node uses to ground its
    LLM call; the LLM's free-form judgement can still override it.
    """
    net_additions = max(0, delta_params) + max(0, delta_components)
    if net_additions <= 0:
        return False
    lift = aggregate_score - baseline_aggregate_score
    if lift <= 0:
        return True
    return (lift / net_additions) < lift_per_addition_floor


# ---------------------------------------------------------------------------
# Deterministic critic — useful as a default, as the unit-test fixture,
# and as a fallback when no LLM client is configured.
# ---------------------------------------------------------------------------


class DeterministicVerdictCritic:
    """Pure-Python critic that applies the structured heuristics.

    Used by tests and as a no-LLM fallback. Decision rules:

    - if any side-effect breaches the envelope → reject
    - if the rationale predicted no side effects but flags fired → reject
    - if complexity-cost asymmetry → reject
    - otherwise accept

    The same logic appears in the LLM prompt as structured guidance;
    keeping a deterministic mirror makes the verdict layer testable
    without network access.
    """

    def critique_verdict(
        self,
        payload: VerdictCritiqueInput,
        *,
        model: ReasoningModel,
    ) -> VerdictCritiqueDecision:
        del model  # protocol parity; deterministic critic ignores the model
        reasons: list[str] = []
        detail: dict[str, Any] = {
            "candidate_name": payload.candidate_name,
            "aggregate_score": payload.aggregate_score,
            "baseline_aggregate_score": payload.baseline_aggregate_score,
            "delta_params": payload.delta_params,
            "delta_components": payload.delta_components,
            "side_effect_flags": list(payload.side_effect_flags),
        }

        breaches = side_effect_envelope_breach(
            payload.side_effect_flags, payload.expected_side_effect_envelope
        )
        if breaches:
            reasons.append("side_effect_envelope")
            detail["side_effect_breaches"] = breaches

        if not payload.stage1_idea.expected_side_effects and payload.side_effect_flags:
            reasons.append("rationale_result_mismatch")
            detail["unexpected_side_effects"] = list(payload.side_effect_flags)

        if complexity_cost_asymmetry(
            aggregate_score=payload.aggregate_score,
            baseline_aggregate_score=payload.baseline_aggregate_score,
            delta_params=payload.delta_params,
            delta_components=payload.delta_components,
        ):
            reasons.append("complexity_cost")
            detail["lift_per_addition"] = _lift_per_addition(payload)

        if payload.mechanical_gate is not None and payload.mechanical_gate.borderline:
            # Borderline does not automatically reject; it signals that
            # this critic SHOULD have stricter scrutiny if other reasons
            # are already firing. Reflect that in the detail.
            detail["mechanical_gate_borderline"] = True

        accept = not reasons
        rationale = (
            "verdict-critique accepted"
            if accept
            else "verdict-critique rejected: " + ", ".join(reasons)
        )
        return VerdictCritiqueDecision(
            accept=accept,
            reasons=reasons,
            rationale=rationale,
            detail=detail,
        )


def _lift_per_addition(payload: VerdictCritiqueInput) -> float:
    net_additions = max(0, payload.delta_params) + max(0, payload.delta_components)
    if net_additions == 0:
        return 0.0
    return (payload.aggregate_score - payload.baseline_aggregate_score) / net_additions


# ---------------------------------------------------------------------------
# Node wrapper
# ---------------------------------------------------------------------------


def verdict_critique_node(
    *,
    payload: VerdictCritiqueInput,
    client: VerdictCritiqueClient,
    model: ReasoningModel,
    mechanical_gate_outcome: MechanicalGateOutcome,
) -> tuple[VerdictCritiqueDecision, RejectKind | None]:
    """Run the verdict-critique node and return its decision.

    Invariant: a mechanical-gate REJECT must never reach this node.
    Callers must check ``mechanical_gate_outcome.accept`` first; the
    node panics deliberately if it sees a rejected gate, satisfying
    ``hypothesis-loop::verdict-critique-with-no-gate-override``.

    Returns ``(decision, reject_kind)`` where ``reject_kind`` is
    :data:`RejectKind.REJECT_VERDICT` when the LLM rejected the
    candidate, otherwise ``None`` (acceptance). The orchestrator uses
    the kind to drive the next persisted ``DecisionRecord``.
    """
    if not mechanical_gate_outcome.accept:
        msg = (
            "verdict_critique_node invoked with a rejected mechanical gate "
            f"(reject_kind={mechanical_gate_outcome.reject_kind}); the gate "
            "is a hard floor and MUST not be overridden by this node"
        )
        raise RuntimeError(msg)
    decision = client.critique_verdict(payload, model=model)
    if decision.accept:
        return decision, None
    return decision, RejectKind.REJECT_VERDICT


def build_verdict_rationale(decision: VerdictCritiqueDecision) -> dict[str, Any]:
    """Project a verdict-critique decision to ledger evidence shape."""
    rationale = verdict_rationale(
        reason=decision.rationale,
        detail={"reasons": list(decision.reasons), **decision.detail},
    )
    return rationale.to_evidence_dict()


__all__ = [
    "DeterministicVerdictCritic",
    "VerdictCritiqueClient",
    "VerdictCritiqueDecision",
    "VerdictCritiqueInput",
    "build_verdict_rationale",
    "complexity_cost_asymmetry",
    "side_effect_envelope_breach",
    "verdict_critique_node",
]
