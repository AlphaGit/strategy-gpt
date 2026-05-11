"""Reasoning-model configuration for the Hypothesis Loop.

Satisfies `hypothesis-loop::reasoning-model-usage`: the workflow defaults
to the most capable reasoning model available at runtime, and an explicit
configuration overrides that choice for every reasoning call (today: the
``diagnose`` narrative and ``critique`` node). The actual LLM client lives
elsewhere; this module is a value-object + selector so node implementations
take a single :class:`ReasoningModel` parameter rather than re-implementing
provider detection each time.

Selection policy (ranked, most capable first):

1. ``claude-opus-4-7`` — preferred when ``ANTHROPIC_API_KEY`` is set.
2. ``claude-sonnet-4-6`` — second-tier Anthropic reasoning model.
3. ``o3`` — preferred when ``OPENAI_API_KEY`` is set and Anthropic is not.
4. ``o1`` — fallback OpenAI reasoning model.

The ranking is encoded explicitly rather than read from a server-side
catalog: the loop must be deterministic and offline-runnable in tests,
and the cost of stale entries here is bounded (override via
:func:`select_reasoning_model` arguments or environment).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

Provider = Literal["anthropic", "openai"]


@dataclass(frozen=True, slots=True)
class ReasoningModel:
    """Provider + model identifier carried into every reasoning call."""

    provider: Provider
    model_id: str


# Ranked preference table. The first entry whose env-key is present wins.
# Anthropic models rank first because Opus 4.7 is the most capable reasoning
# model at the rewrite's January 2026 cutoff; the OpenAI ``o3`` family is
# the fallback for environments that only carry OpenAI credentials.
_REASONING_PRIORITY: Final[tuple[tuple[Provider, str, str], ...]] = (
    ("anthropic", "claude-opus-4-7", "ANTHROPIC_API_KEY"),
    ("anthropic", "claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
    ("openai", "o3", "OPENAI_API_KEY"),
    ("openai", "o1", "OPENAI_API_KEY"),
)


class NoReasoningModelAvailableError(RuntimeError):
    """Raised when no provider env-key is set and no override is supplied."""


def select_reasoning_model(
    *,
    override: ReasoningModel | None = None,
    env: dict[str, str] | None = None,
) -> ReasoningModel:
    """Return the active reasoning model.

    `override` short-circuits the lookup so callers configuring the loop
    explicitly always win (`hypothesis-loop::configured-model-is-honored`).
    Otherwise the function walks :data:`_REASONING_PRIORITY` in order
    and returns the first entry whose env-key is set in ``env``
    (defaults to :data:`os.environ`). Raises
    :class:`NoReasoningModelAvailableError` if nothing matches — this is
    the failure mode the workflow surfaces to the operator at startup
    rather than at first reasoning call.
    """
    if override is not None:
        return override
    available = env if env is not None else os.environ
    for provider, model_id, env_key in _REASONING_PRIORITY:
        if available.get(env_key):
            return ReasoningModel(provider=provider, model_id=model_id)
    msg = (
        "no reasoning model available: set ANTHROPIC_API_KEY or "
        "OPENAI_API_KEY, or pass `override=` to select_reasoning_model"
    )
    raise NoReasoningModelAvailableError(msg)


@dataclass(frozen=True, slots=True)
class HypothesisLoopConfig:
    """Knobs the Hypothesis Loop reads at workflow start.

    Kept here (rather than in :mod:`strategy_gpt.hypothesis_loop`)
    because it composes :class:`ReasoningModel`; co-locating avoids an
    import cycle if the loop module later imports the config back into
    state.

    `iteration_budget` and `target_candidates` parameterise the
    `hypothesis-loop::internal-iteration-loop` termination conditions;
    `similarity_threshold` is the cosine cutoff above which a generated
    candidate is treated as a duplicate of a prior decision and the loop
    records ``similarity_saturation``. Concrete consumers land with
    9.4 / 9.5 / 9.8.
    """

    reasoning_model: ReasoningModel
    iteration_budget: int = 4
    target_candidates: int = 3
    similarity_threshold: float = 0.85

    @classmethod
    def with_defaults(
        cls,
        *,
        reasoning_model: ReasoningModel | None = None,
        iteration_budget: int = 4,
        target_candidates: int = 3,
        similarity_threshold: float = 0.85,
        env: dict[str, str] | None = None,
    ) -> HypothesisLoopConfig:
        """Build a config, defaulting unspecified knobs and resolving the
        reasoning model from the environment when none is supplied."""
        model = reasoning_model if reasoning_model is not None else select_reasoning_model(env=env)
        return cls(
            reasoning_model=model,
            iteration_budget=iteration_budget,
            target_candidates=target_candidates,
            similarity_threshold=similarity_threshold,
        )


__all__ = [
    "HypothesisLoopConfig",
    "NoReasoningModelAvailableError",
    "Provider",
    "ReasoningModel",
    "select_reasoning_model",
]
