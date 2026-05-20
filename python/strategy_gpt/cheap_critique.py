"""Cheap-critique node — idea-level rejection before stage 2/3.

Runs immediately after a successful stage-1 emission and BEFORE stages 2
and 3 are invoked (`hypothesis-loop::cheap-critique-runs-after-stage-1`).
Catches four failure modes deterministically, with no LLM call:

1. **Malformed idea** — empty name or empty rationale. The strict
   :mod:`strategy_gpt.markdown_io` parser already enforces basic
   well-formedness; this catches the residual cases where the parser
   accepts a vacuous value (e.g., whitespace-only rationale after
   trimming).
2. **Duplicate of a prior rejected hypothesis** — Jaccard-token overlap
   over name + rationale exceeds a configured similarity threshold
   against any prior rejection. The rationale records the offending
   ``decision_id`` so subsequent iterations can surface why the idea
   was killed.
3. **Contradicts diagnosis** — the idea's targeted side-effect is the
   opposite direction of a clearly-observed diagnosis signal. Today the
   check is conservative: it flags candidates whose rationale promises
   to *increase trade count* when the diagnosis shows trade-count
   already high relative to fired-no-trade misfires (i.e., the
   strategy has plenty of activity; adding more is unlikely to help).
   Designed to be opt-in via configuration as more heuristics land.
4. **Violates an accepted prior** — the idea proposes removing or
   inverting a component an earlier accepted decision explicitly
   added. Detected by matching the candidate name against accepted
   priors' ``proposed_change.removes`` / ``proposed_change.added`` keys.

The node is deterministic and replay-safe; the rationale strings are
stable given the same inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .diagnose import Diagnosis
from .hypothesis_loop import PriorDecision
from .markdown_io import Stage1Idea
from .types import DecisionKind

# ---------------------------------------------------------------------------
# Tokenization (reused from nodes.py default_similarity intent)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_SPLIT_RE = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Coarse tokens that keep snake_case identifiers whole."""
    return {tok.lower() for tok in _TOKEN_RE.findall(text)}


def _name_tokens(text: str) -> set[str]:
    """Fine tokens that split snake_case identifiers on `_`.

    Used for cross-matching when a prior decision's name is referenced
    inside free-form prose, where the underscore form may not appear
    verbatim (e.g., prior name ``hedge_leg`` vs. rationale prose
    ``hedge leg``).
    """
    return {tok.lower() for tok in _SPLIT_RE.findall(text)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _idea_signature(idea: Stage1Idea) -> set[str]:
    """Bag-of-tokens over name + rationale."""
    return _tokens(idea.candidate_name) | _tokens(idea.rationale)


def _prior_signature(prior: PriorDecision) -> set[str]:
    """Same tokenization rule applied to a prior decision."""
    name = prior.hypothesis.name
    # Prior rationale is on the decision record, not the hypothesis.
    rationale = prior.rationale or ""
    return _tokens(name) | _tokens(rationale)


# ---------------------------------------------------------------------------
# Outcome shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheapCritiqueOutcome:
    """Result of running cheap-critique on a single stage-1 idea.

    ``accept`` is ``True`` when the idea survives all four checks.
    ``reason`` carries a short tag identifying which check fired
    (one of ``ok``, ``malformed``, ``duplicate_of_prior_reject``,
    ``contradicts_diagnosis``, ``violates_prior_accept``). ``rationale``
    is the human-readable explanation persisted on the decision record
    when the idea is rejected.
    """

    accept: bool
    reason: str
    rationale: str
    matched_decision_id: str | None = None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_malformed(idea: Stage1Idea) -> CheapCritiqueOutcome | None:
    if not idea.candidate_name.strip():
        return CheapCritiqueOutcome(
            accept=False,
            reason="malformed",
            rationale="stage-1 idea has empty candidate_name after trim",
        )
    if not idea.rationale.strip():
        return CheapCritiqueOutcome(
            accept=False,
            reason="malformed",
            rationale="stage-1 idea has empty rationale after trim",
        )
    if not 0.0 <= idea.expected_lift_confidence <= 1.0:
        return CheapCritiqueOutcome(
            accept=False,
            reason="malformed",
            rationale=(
                f"expected_lift_confidence {idea.expected_lift_confidence} outside [0.0, 1.0]"
            ),
        )
    return None


def _check_duplicate(
    idea: Stage1Idea,
    priors: list[PriorDecision],
    *,
    threshold: float,
) -> CheapCritiqueOutcome | None:
    if not priors:
        return None
    sig = _idea_signature(idea)
    best: tuple[float, PriorDecision] | None = None
    for prior in priors:
        if prior.kind is not DecisionKind.REJECTED:
            continue
        score = _jaccard(sig, _prior_signature(prior))
        if best is None or score > best[0]:
            best = (score, prior)
    if best is None:
        return None
    score, prior = best
    if score >= threshold:
        return CheapCritiqueOutcome(
            accept=False,
            reason="duplicate_of_prior_reject",
            rationale=(
                f"stage-1 idea overlaps prior rejected decision {prior.decision_id} "
                f"(jaccard={score:.2f} ≥ threshold {threshold:.2f}); "
                f"prior name: `{prior.hypothesis.name}`"
            ),
            matched_decision_id=prior.decision_id,
        )
    return None


_INCREASE_KEYWORDS = (
    "more trades",
    "increase trade count",
    "increase trade frequency",
    "more entries",
    "fire more often",
)


def _check_contradicts_diagnosis(
    idea: Stage1Idea,
    diagnosis: Diagnosis | None,
) -> CheapCritiqueOutcome | None:
    if diagnosis is None:
        return None
    rationale_lower = idea.rationale.lower()
    if not any(kw in rationale_lower for kw in _INCREASE_KEYWORDS):
        return None
    # If the strategy already trades a lot and has more fired-no-trade
    # than used signals, "more activity" is unlikely to help.
    fired_no_trade = sum(sm.fired_no_trade_count for sm in diagnosis.signal_misfires)
    used = sum(sm.used_count for sm in diagnosis.signal_misfires)
    if used == 0 or fired_no_trade <= used:
        return None
    return CheapCritiqueOutcome(
        accept=False,
        reason="contradicts_diagnosis",
        rationale=(
            "stage-1 idea proposes increasing trade activity but the diagnosis "
            f"shows fired_no_trade ({fired_no_trade}) already dominates used "
            f"({used}) — the signal channel is over-firing, not under-firing"
        ),
    )


def _check_violates_prior_accept(
    idea: Stage1Idea,
    priors: list[PriorDecision],
) -> CheapCritiqueOutcome | None:
    """Reject if the idea proposes to undo a component an accepted prior added.

    Conservative match: looks for ``accepted_prior.hypothesis.name`` appearing
    in the current idea's rationale or name alongside removal-language
    ("remove", "revert", "drop"). False positives are possible; the loop
    can always re-emit a re-framed version.
    """
    if not priors:
        return None
    rationale_lower = (idea.rationale + " " + idea.candidate_name).lower()
    removal_signal = any(
        kw in rationale_lower for kw in ("remove", "revert", "drop", "undo", "delete")
    )
    if not removal_signal:
        return None
    for prior in priors:
        if prior.kind is not DecisionKind.ACCEPTED:
            continue
        name_tokens = _name_tokens(prior.hypothesis.name)
        if name_tokens and name_tokens.issubset(_name_tokens(rationale_lower)):
            return CheapCritiqueOutcome(
                accept=False,
                reason="violates_prior_accept",
                rationale=(
                    f"stage-1 idea proposes undoing accepted prior decision "
                    f"{prior.decision_id} (`{prior.hypothesis.name}`)"
                ),
                matched_decision_id=prior.decision_id,
            )
    return None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def cheap_critique(
    idea: Stage1Idea,
    *,
    prior_decisions: list[PriorDecision],
    diagnosis: Diagnosis | None,
    similarity_threshold: float = 0.7,
) -> CheapCritiqueOutcome:
    """Run idea-level rejection checks.

    Returns an outcome with ``accept=True`` and ``reason='ok'`` when the
    idea passes all four checks. The checks fire in deterministic order:
    malformed → duplicate → contradicts_diagnosis → violates_prior_accept.
    The first failing check wins; later checks are not evaluated, so
    rejection rationale is stable across replays.
    """
    for check in (
        _check_malformed(idea),
        _check_duplicate(idea, prior_decisions, threshold=similarity_threshold),
        _check_contradicts_diagnosis(idea, diagnosis),
        _check_violates_prior_accept(idea, prior_decisions),
    ):
        if check is not None:
            return check
    return CheapCritiqueOutcome(accept=True, reason="ok", rationale="cheap-critique passed")


__all__ = [
    "CheapCritiqueOutcome",
    "cheap_critique",
]
