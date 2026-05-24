"""Tests for the per-stage repair loop driver."""

from __future__ import annotations

import pytest

from strategy_gpt.repair import (
    RepairConfig,
    ValidationOutcome,
    run_stage_with_repair,
    synthesize_repair_feedback,
)


def test_initial_success_no_repairs() -> None:
    def emit(_: str) -> str:
        return "good"

    def validate(_: str) -> ValidationOutcome:
        return ValidationOutcome(ok=True, parsed="parsed")

    result = run_stage_with_repair(stage=1, emit_fn=emit, validate_fn=validate)
    assert result.accepted is True
    assert result.attempts_count == 1
    assert result.final_parsed == "parsed"


def test_recovers_after_first_repair() -> None:
    calls = [0]

    def emit(feedback: str) -> str:
        calls[0] += 1
        return f"attempt-{calls[0]}-feedback:{bool(feedback)}"

    def validate(response: str) -> ValidationOutcome:
        if "attempt-1" in response:
            return ValidationOutcome(
                ok=False,
                kind="reject_format",
                feedback="malformed YAML on line 3",
            )
        return ValidationOutcome(ok=True, parsed=response)

    result = run_stage_with_repair(
        stage=1,
        emit_fn=emit,
        validate_fn=validate,
        config=RepairConfig(k_repair=2),
    )
    assert result.accepted is True
    assert result.attempts_count == 2
    # First attempt's emit received empty feedback; second received synthesized feedback.
    assert "feedback:False" in result.attempts[0].response
    assert "feedback:True" in result.attempts[1].response


def test_exhausts_repair_budget() -> None:
    def emit(_: str) -> str:
        return "always-bad"

    def validate(_: str) -> ValidationOutcome:
        return ValidationOutcome(ok=False, kind="reject_build", feedback="rustc said no")

    result = run_stage_with_repair(
        stage=3,
        emit_fn=emit,
        validate_fn=validate,
        config=RepairConfig(k_repair=2),
    )
    assert result.accepted is False
    # K_repair=2 means 3 total attempts (1 initial + 2 repairs).
    assert result.attempts_count == 3
    assert result.final_reject_kind == "reject_build"


def test_k_repair_zero_means_single_attempt() -> None:
    def emit(_: str) -> str:
        return "x"

    def validate(_: str) -> ValidationOutcome:
        return ValidationOutcome(ok=False, kind="reject_format", feedback="bad")

    result = run_stage_with_repair(
        stage=1,
        emit_fn=emit,
        validate_fn=validate,
        config=RepairConfig(k_repair=0),
    )
    assert result.accepted is False
    assert result.attempts_count == 1


def test_synthesize_feedback_mentions_stage_and_kind() -> None:
    outcome = ValidationOutcome(
        ok=False,
        kind="reject_format",
        feedback="missing fenced block after `## src/lib.rs`",
    )
    text = synthesize_repair_feedback(outcome, stage=3)
    assert "stage-3" in text
    assert "reject_format" in text
    assert "missing fenced block" in text
    assert "Earlier stages" in text


def test_synthesize_feedback_embeds_previous_emission() -> None:
    outcome = ValidationOutcome(
        ok=False,
        kind="reject_build",
        feedback="error[E0425]: cannot find value `foo` in this scope",
    )
    prev = "## src/lib.rs\n```rust\nfn run() { foo(); }\n```\n"
    text = synthesize_repair_feedback(outcome, stage=3, prev_response=prev)
    assert "PREVIOUS_EMISSION" in text
    assert "fn run() { foo(); }" in text
    assert "E0425" in text


def test_repair_loop_threads_previous_response_into_feedback() -> None:
    feedbacks: list[str] = []

    def emit(feedback: str) -> str:
        feedbacks.append(feedback)
        return f"attempt-{len(feedbacks)}: emitted source"

    outcomes = iter(
        [
            ValidationOutcome(ok=False, kind="reject_build", feedback="rustc error X"),
            ValidationOutcome(ok=False, kind="reject_build", feedback="rustc error Y"),
            ValidationOutcome(ok=True, kind="ok", parsed={"files": {}}),
        ]
    )

    def validate(_response: str) -> ValidationOutcome:
        return next(outcomes)

    result = run_stage_with_repair(
        stage=3,
        emit_fn=emit,
        validate_fn=validate,
        config=RepairConfig(k_repair=2),
    )
    assert result.accepted
    # First attempt is the initial call (empty feedback). Subsequent
    # attempts must carry the previous emission verbatim so the LLM can
    # patch the broken file in place.
    assert feedbacks[0] == ""
    assert "attempt-1: emitted source" in feedbacks[1]
    assert "rustc error X" in feedbacks[1]
    assert "attempt-2: emitted source" in feedbacks[2]
    assert "rustc error Y" in feedbacks[2]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
