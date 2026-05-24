"""Per-stage repair loop for the hypothesis-loop candidate emission path.

Implements `hypothesis-loop::repair-loop-per-stage`: each generate stage
gets a bounded repair budget (``K_repair = 2`` by default). Failures are
classified, feedback is synthesized, and the stage is re-emitted up to
the budget. Earlier-stage commitments are NEVER re-opened by repair —
see ADR 0019.

This module is orchestration-only. It does not own:

- The reasoning-client call itself (callers inject an emitter via
  ``emit_fn`` so tests can stub the LLM behaviour).
- The validation logic (callers inject ``validate_fn`` returning a
  ``ValidationOutcome``). The stage-3 validator that wires
  build-pipeline + cargo lints lives in :mod:`strategy_gpt.validation`.

Each repair attempt is persisted as a :class:`RepairAttempt` so the
downstream ``DecisionRecord.evidence.attempts`` array (per spec) can
reconstruct exactly what was tried, how it failed, and what feedback
the next attempt received.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


RejectKind = str  # one of: reject_format / reject_build / reject_lint /
#                          reject_schema / reject_smoke


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Result of validating a stage emission.

    ``ok`` is ``True`` when the emission passed all checks the validator
    runs for that stage. When ``False``, ``kind`` carries one of the
    structural reject tags (``reject_format``, ``reject_build``,
    ``reject_schema``, …) and ``feedback`` is the synthesized error
    message that will be embedded into the next attempt's user prompt.
    ``parsed`` is the parsed payload on success — its concrete type is
    stage-specific (``Stage1Idea``, ``Stage2Commitments``, ``Stage3Files``,
    or any caller-defined wrapper).
    """

    ok: bool
    kind: RejectKind = "ok"
    feedback: str = ""
    parsed: object = None


@dataclass(frozen=True, slots=True)
class RepairAttempt:
    """One attempt within a stage's repair budget.

    Persisted to ``DecisionRecord.evidence.attempts`` per the spec.
    ``index`` is 0 for the initial emission, 1 for the first repair,
    etc. ``response`` is the raw stage emission text. ``outcome`` is
    the validator's verdict.
    """

    index: int
    response: str
    outcome: ValidationOutcome


T = TypeVar("T")


@dataclass(slots=True)
class StageRepairResult(Generic[T]):
    """Final result of running a stage with its repair budget."""

    stage: int
    accepted: bool
    final_response: str
    final_parsed: T | None
    final_reject_kind: RejectKind
    attempts: list[RepairAttempt] = field(default_factory=list)

    @property
    def attempts_count(self) -> int:
        return len(self.attempts)


# ---------------------------------------------------------------------------
# Feedback synthesizers
# ---------------------------------------------------------------------------


_PREV_RESPONSE_MAX_CHARS = 8000


def synthesize_repair_feedback(
    outcome: ValidationOutcome,
    *,
    stage: int,
    prev_response: str | None = None,
) -> str:
    """Build the user-facing feedback for the next attempt.

    Centralized so the format stays consistent across stages. The model
    sees the reject kind, the validator's error message, and (when
    available) the verbatim previous attempt so it can edit-in-place
    instead of starting from scratch.
    """
    parts = [
        f"Your previous stage-{stage} attempt was rejected as `{outcome.kind}`.",
        outcome.feedback or "(no validator feedback)",
    ]
    if prev_response:
        truncated = prev_response
        if len(truncated) > _PREV_RESPONSE_MAX_CHARS:
            head = truncated[: _PREV_RESPONSE_MAX_CHARS // 2]
            tail = truncated[-_PREV_RESPONSE_MAX_CHARS // 2 :]
            truncated = f"{head}\n... [truncated] ...\n{tail}"
        parts.extend(
            [
                "",
                "Your previous emission was:",
                "<<<PREVIOUS_EMISSION",
                truncated,
                "PREVIOUS_EMISSION>>>",
            ]
        )
    parts.extend(
        [
            "",
            "Re-emit the same stage following the same contract. Fix the "
            "specific issue(s) named above; keep everything that already "
            "worked. Earlier stages (if any) remain locked and MUST NOT be "
            "re-opened.",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairConfig:
    """Repair-loop configuration."""

    k_repair: int = 2


EmitFn = Callable[[str], str]
"""Stage emitter. Takes the optional repair-feedback string (empty on
the initial attempt) and returns the raw stage emission text."""

ValidateFn = Callable[[str], ValidationOutcome]
"""Stage validator. Takes the raw emission and returns a verdict."""


_DEFAULT_REPAIR_CONFIG = RepairConfig()


def run_stage_with_repair(
    *,
    stage: int,
    emit_fn: EmitFn,
    validate_fn: ValidateFn,
    config: RepairConfig = _DEFAULT_REPAIR_CONFIG,
) -> StageRepairResult[object]:
    """Run one stage with its repair budget.

    Calls ``emit_fn("")`` for the initial attempt, then up to
    ``config.k_repair`` more attempts each receiving the synthesized
    feedback from the previous failure. Returns a populated
    :class:`StageRepairResult`; the ``accepted`` flag indicates whether
    a passing emission was found within the budget.

    Mechanical-gate failures and verdict-critique rejections MUST NOT
    trigger repair attempts (see spec). The validator should never
    return those kinds; the orchestrator above this function is
    responsible for honoring that boundary.
    """
    attempts: list[RepairAttempt] = []
    feedback = ""

    for attempt_idx in range(config.k_repair + 1):
        response = emit_fn(feedback)
        outcome = validate_fn(response)
        attempts.append(RepairAttempt(index=attempt_idx, response=response, outcome=outcome))
        if outcome.ok:
            return StageRepairResult(
                stage=stage,
                accepted=True,
                final_response=response,
                final_parsed=outcome.parsed,
                final_reject_kind="ok",
                attempts=attempts,
            )
        feedback = synthesize_repair_feedback(
            outcome,
            stage=stage,
            prev_response=response,
        )

    last = attempts[-1]
    return StageRepairResult(
        stage=stage,
        accepted=False,
        final_response=last.response,
        final_parsed=None,
        final_reject_kind=last.outcome.kind or "exhausted_repair_budget",
        attempts=attempts,
    )


__all__ = [
    "EmitFn",
    "RejectKind",
    "RepairAttempt",
    "RepairConfig",
    "StageRepairResult",
    "ValidateFn",
    "ValidationOutcome",
    "run_stage_with_repair",
    "synthesize_repair_feedback",
]
