"""Tests for the verdict-critique node."""

from __future__ import annotations

import pytest

from strategy_gpt.markdown_io import Stage1Idea
from strategy_gpt.mechanical_gate import MechanicalGateConfig, mechanical_gate
from strategy_gpt.reasoning import ReasoningModel
from strategy_gpt.reject_taxonomy import RejectKind
from strategy_gpt.verdict_critique import (
    DeterministicVerdictCritic,
    VerdictCritiqueInput,
    complexity_cost_asymmetry,
    side_effect_envelope_breach,
    verdict_critique_node,
)

_MODEL = ReasoningModel(provider="anthropic", model_id="claude-opus-4-7")


def _idea(side_effects: list[str] | None = None) -> Stage1Idea:
    effects = side_effects if side_effects is not None else ["trade_count_up_30pct"]
    return Stage1Idea(
        candidate_name="add_hedge",
        rationale="Add a treasury hedge leg to dampen drawdowns.",
        expected_lift_confidence=0.6,
        expected_side_effects=effects,
    )


def _gate_accept():
    return mechanical_gate(
        candidate_fold_scores=[1.5, 1.5, 1.55],
        baseline_fold_scores=[1.0, 1.0, 1.05],
    )


def test_deterministic_critic_accepts_clean_candidate() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="add_hedge",
        stage1_idea=_idea(),
        aggregate_score=1.5,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.5, 1.5, 1.5],
        baseline_per_fold_scores=[1.0, 1.0, 1.0],
        side_effect_flags=[],
        delta_params=1,
        delta_components=0,
    )
    critic = DeterministicVerdictCritic()
    decision = critic.critique_verdict(payload, model=_MODEL)
    assert decision.accept is True
    assert decision.reasons == []


def test_critic_rejects_when_side_effects_breach_envelope() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="add_hedge",
        stage1_idea=_idea(),
        aggregate_score=1.5,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.5, 1.5, 1.5],
        baseline_per_fold_scores=[1.0, 1.0, 1.0],
        side_effect_flags=["n_trades_up_3.00x"],
        expected_side_effect_envelope={"n_trades": (0.5, 1.5)},
    )
    critic = DeterministicVerdictCritic()
    decision = critic.critique_verdict(payload, model=_MODEL)
    assert decision.accept is False
    assert "side_effect_envelope" in decision.reasons


def test_critic_rejects_unexpected_side_effects_when_idea_promised_none() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="add_hedge",
        stage1_idea=_idea(side_effects=[]),
        aggregate_score=1.5,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.5],
        baseline_per_fold_scores=[1.0],
        side_effect_flags=["max_drawdown_up_1.6x"],
    )
    critic = DeterministicVerdictCritic()
    decision = critic.critique_verdict(payload, model=_MODEL)
    assert decision.accept is False
    assert "rationale_result_mismatch" in decision.reasons


def test_critic_rejects_complexity_cost_asymmetry() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="bloat",
        stage1_idea=_idea(),
        aggregate_score=1.001,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.001],
        baseline_per_fold_scores=[1.0],
        side_effect_flags=[],
        delta_params=10,
        delta_components=3,
    )
    critic = DeterministicVerdictCritic()
    decision = critic.critique_verdict(payload, model=_MODEL)
    assert decision.accept is False
    assert "complexity_cost" in decision.reasons


def test_envelope_breach_helper_ignores_unknown_metrics() -> None:
    breaches = side_effect_envelope_breach(
        ["sortino_up_3.00x"],
        envelope={"n_trades": (0.5, 1.5)},
    )
    assert breaches == []


def test_complexity_cost_asymmetry_returns_false_for_pure_removal() -> None:
    assert (
        complexity_cost_asymmetry(
            aggregate_score=1.1,
            baseline_aggregate_score=1.0,
            delta_params=-2,
            delta_components=0,
        )
        is False
    )


def test_node_raises_on_rejected_gate() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="x",
        stage1_idea=_idea(),
        aggregate_score=1.0,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.0],
        baseline_per_fold_scores=[1.0],
        side_effect_flags=[],
    )
    gate = mechanical_gate(
        candidate_fold_scores=[0.5, 5.0, 0.5, 5.0],
        baseline_fold_scores=[1.0, 1.0, 1.0, 1.0],
        config=MechanicalGateConfig(k=0.01, fold_cv_threshold=0.5),
    )
    assert gate.accept is False
    critic = DeterministicVerdictCritic()
    with pytest.raises(RuntimeError, match="rejected mechanical gate"):
        verdict_critique_node(
            payload=payload,
            client=critic,
            model=_MODEL,
            mechanical_gate_outcome=gate,
        )


def test_node_returns_reject_verdict_kind_on_llm_reject() -> None:
    payload = VerdictCritiqueInput(
        candidate_name="x",
        stage1_idea=_idea(side_effects=[]),
        aggregate_score=1.5,
        baseline_aggregate_score=1.0,
        per_fold_scores=[1.5],
        baseline_per_fold_scores=[1.0],
        side_effect_flags=["max_drawdown_up_2.0x"],
    )
    gate = _gate_accept()
    decision, kind = verdict_critique_node(
        payload=payload,
        client=DeterministicVerdictCritic(),
        model=_MODEL,
        mechanical_gate_outcome=gate,
    )
    assert decision.accept is False
    assert kind is RejectKind.REJECT_VERDICT
