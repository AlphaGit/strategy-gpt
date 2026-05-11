"""Reasoning-model selection tests."""

from __future__ import annotations

import pytest

from strategy_gpt.reasoning import (
    HypothesisLoopConfig,
    NoReasoningModelAvailableError,
    ReasoningModel,
    select_reasoning_model,
)


def test_anthropic_key_picks_opus_4_7() -> None:
    model = select_reasoning_model(env={"ANTHROPIC_API_KEY": "sk-x"})
    assert model == ReasoningModel(provider="anthropic", model_id="claude-opus-4-7")


def test_openai_only_falls_back_to_o3() -> None:
    model = select_reasoning_model(env={"OPENAI_API_KEY": "sk-y"})
    assert model == ReasoningModel(provider="openai", model_id="o3")


def test_anthropic_outranks_openai_when_both_set() -> None:
    model = select_reasoning_model(env={"ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y"})
    assert model.provider == "anthropic"
    assert model.model_id == "claude-opus-4-7"


def test_empty_env_raises_no_model_available() -> None:
    with pytest.raises(NoReasoningModelAvailableError):
        select_reasoning_model(env={})


def test_override_short_circuits_environment_lookup() -> None:
    override = ReasoningModel(provider="openai", model_id="o1")
    model = select_reasoning_model(override=override, env={"ANTHROPIC_API_KEY": "sk-x"})
    assert model is override


def test_config_with_defaults_resolves_reasoning_model() -> None:
    cfg = HypothesisLoopConfig.with_defaults(env={"ANTHROPIC_API_KEY": "sk-x"})
    assert cfg.reasoning_model.provider == "anthropic"
    assert cfg.iteration_budget == 4
    assert cfg.target_candidates == 3
    assert cfg.similarity_threshold == pytest.approx(0.85)


def test_config_with_defaults_overrides_knobs() -> None:
    override = ReasoningModel(provider="openai", model_id="o3")
    cfg = HypothesisLoopConfig.with_defaults(
        reasoning_model=override,
        iteration_budget=10,
        target_candidates=8,
        similarity_threshold=0.95,
    )
    assert cfg.reasoning_model is override
    assert cfg.iteration_budget == 10
    assert cfg.target_candidates == 8
    assert cfg.similarity_threshold == pytest.approx(0.95)


def test_blank_env_key_is_treated_as_unset() -> None:
    # API keys set to the empty string (a common .env trap) should fail
    # rather than silently route to a provider that will reject the call.
    with pytest.raises(NoReasoningModelAvailableError):
        select_reasoning_model(env={"ANTHROPIC_API_KEY": ""})
