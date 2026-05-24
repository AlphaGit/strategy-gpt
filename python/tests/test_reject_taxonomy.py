"""Tests for the consolidated reject taxonomy."""

from __future__ import annotations

from strategy_gpt.reject_taxonomy import (
    RejectKind,
    deps_rationale,
    is_mechanical,
    is_repairable,
    noise_rationale,
    schema_rationale,
    variance_rationale,
    verdict_rationale,
)


def test_repairable_predicate_distinguishes_structural_from_terminal() -> None:
    assert is_repairable(RejectKind.REJECT_FORMAT)
    assert is_repairable(RejectKind.REJECT_BUILD)
    assert is_repairable("reject_schema")
    assert not is_repairable(RejectKind.REJECT_NOISE)
    assert not is_repairable(RejectKind.REJECT_VARIANCE)
    assert not is_repairable(RejectKind.REJECT_VERDICT)
    assert not is_repairable("unknown-kind")


def test_noise_rationale_captures_full_evidence() -> None:
    r = noise_rationale(score=1.5, baseline_score=1.0, sigma_combined=0.3, k=1.0)
    evidence = r.to_evidence_dict()
    assert evidence["reject_kind"] == "reject_noise"
    assert evidence["score"] == 1.5
    assert evidence["baseline_score"] == 1.0
    assert evidence["sigma_combined"] == 0.3
    assert evidence["k"] == 1.0
    assert "delta" in evidence


def test_variance_rationale_carries_threshold() -> None:
    r = variance_rationale(fold_cv=0.7, threshold=0.5)
    evidence = r.to_evidence_dict()
    assert evidence["reject_kind"] == "reject_variance"
    assert evidence["threshold"] == 0.5
    assert evidence["fold_cv"] == 0.7


def test_schema_rationale_lists_offending_params() -> None:
    r = schema_rationale(missing_added=["hedge_ratio"], leaked_removed=["trail_stop_atr_mult"])
    assert "hedge_ratio" in r.summary
    assert "trail_stop_atr_mult" in r.summary
    assert r.kind is RejectKind.REJECT_SCHEMA


def test_verdict_rationale_includes_detail() -> None:
    r = verdict_rationale(
        reason="side effects exceed envelope",
        detail={"flags": ["n_trades_up_3x"]},
    )
    assert r.kind is RejectKind.REJECT_VERDICT
    evidence = r.to_evidence_dict()
    assert evidence["flags"] == ["n_trades_up_3x"]


def test_mechanical_predicate_distinguishes_code_emission_from_logic() -> None:
    # Mechanical (LLM couldn't compile / format / parse the strategy):
    assert is_mechanical(RejectKind.REJECT_BUILD)
    assert is_mechanical(RejectKind.REJECT_LINT)
    assert is_mechanical(RejectKind.REJECT_FORMAT)
    assert is_mechanical(RejectKind.REJECT_DEPS)
    assert is_mechanical(RejectKind.EXHAUSTED_REPAIR_BUDGET)
    assert is_mechanical("reject_build")
    # Logic-level (the idea/commitments/code behavior is wrong):
    assert not is_mechanical(RejectKind.REJECT_SCHEMA)
    assert not is_mechanical(RejectKind.REJECT_SMOKE)
    assert not is_mechanical(RejectKind.REJECT_NOISE)
    assert not is_mechanical(RejectKind.REJECT_VARIANCE)
    assert not is_mechanical(RejectKind.REJECT_VERDICT)
    # Unknown strings are not mechanical (conservative default).
    assert not is_mechanical("garbage")


def test_deps_rationale_summary_sorted() -> None:
    r = deps_rationale(unlisted_crates=["tokio", "anyhow"])
    # sorted for determinism
    assert "anyhow" in r.summary
    assert r.evidence["unlisted_crates"] == ["anyhow", "tokio"]
