"""Real reasoning-client implementations for Anthropic and OpenAI.

The hypothesis loop calls a reasoning model three times per candidate
(stage 1 → stage 2 → stage 3 emission). Earlier scaffolding used a stub
client returning canned candidates; this module replaces the stub with
real provider integrations and a small dispatch layer that routes calls
based on the configured :class:`ReasoningModel` provider.

Output contract:

- Each ``emit_stage`` call returns the **raw markdown text** for the
  named stage. The strict parser in
  :mod:`strategy_gpt.markdown_io` is the next pipeline step; it owns
  validation. The client's job is to make sure the model emits a single
  markdown payload (no chain-of-thought prose, no apologies) suitable
  for parsing.
- Anthropic clients use a single-tool ``emit_markdown`` whose
  ``markdown`` argument carries the stage text. ``tool_choice`` forces
  the model to call the tool, eliminating freeform side text. This is
  the "structured tool-use enforced shape per stage" that
  ``hypothesis-loop::multi-stage-candidate-emission`` requires.
- OpenAI clients use the Responses API with
  ``response_format={"type": "json_schema", "json_schema": ...}`` to
  force a JSON object with a single ``markdown`` field. The shape
  matches the Anthropic tool-use shape so the dispatcher can treat both
  uniformly downstream.

No network calls happen at import time; constructors only check that
the appropriate API key is present in the environment (or supplied
explicitly).
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol

from .prompts import StagePrompt
from .reasoning import ReasoningModel

Stage = Literal[1, 2, 3]

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class StageReasoningClient(Protocol):
    """Stage-aware reasoning client.

    Returns the verbatim markdown emission for one stage. Implementations
    are responsible for routing the prompt to the configured provider,
    forcing the output shape (tool-use on Anthropic, JSON-schema on
    OpenAI), and extracting the markdown payload.
    """

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: Stage,
        model: ReasoningModel,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


_EMIT_MARKDOWN_TOOL = {
    "name": "emit_markdown",
    "description": (
        "Emit the markdown payload for the current hypothesis-loop stage. "
        "The `markdown` argument MUST contain a single well-formed markdown "
        "document matching the stage contract described in the system prompt. "
        "Do not include any narration outside the markdown payload."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "The stage emission as markdown.",
            },
        },
        "required": ["markdown"],
    },
}


class AnthropicReasoningClient:
    """Anthropic Messages API client.

    Uses a single forced tool (``emit_markdown``) per call so the model
    cannot mix narration with the markdown payload. Compatible with
    ``claude-opus-*`` and ``claude-sonnet-*`` model IDs.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any = None,  # noqa: ANN401 — SDK client; concrete type is `anthropic.Anthropic`
    ) -> None:
        if client is not None:
            self._client = client
            return
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            msg = (
                "AnthropicReasoningClient requires ANTHROPIC_API_KEY in the "
                "environment, an explicit `api_key=`, or an injected `client=`"
            )
            raise RuntimeError(msg)
        import anthropic  # noqa: PLC0415 — lazy SDK import keeps optional dependency out of cold path

        self._client = anthropic.Anthropic(api_key=key)

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: Stage,
        model: ReasoningModel,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str:
        if model.provider != "anthropic":
            msg = f"AnthropicReasoningClient cannot serve {model.provider} model"
            raise ValueError(msg)
        response = self._client.messages.create(
            model=model.model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            system=prompt.system,
            tools=[_EMIT_MARKDOWN_TOOL],
            tool_choice={"type": "tool", "name": "emit_markdown"},
            messages=[{"role": "user", "content": prompt.user}],
        )
        return _extract_anthropic_markdown(response, stage=stage)


def _extract_anthropic_markdown(response: Any, *, stage: Stage) -> str:  # noqa: ANN401
    """Extract the ``markdown`` field from the forced tool call.

    Anthropic SDK returns ``response.content`` as a list of content
    blocks. The forced ``emit_markdown`` tool produces a single
    ``tool_use`` block whose ``input`` carries the payload.
    """
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        # Both real SDK objects and our test stubs expose `.type`.
        if getattr(block, "type", None) == "tool_use":
            payload = getattr(block, "input", {}) or {}
            md = payload.get("markdown")
            if isinstance(md, str) and md.strip():
                return md
    msg = f"Anthropic stage-{stage} response did not contain an emit_markdown tool_use block"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


_MARKDOWN_JSON_SCHEMA: dict[str, Any] = {
    "name": "stage_emission",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "markdown": {
                "type": "string",
                "description": "The stage emission as markdown.",
            },
        },
        "required": ["markdown"],
    },
    "strict": True,
}


class OpenAIReasoningClient:
    """OpenAI Responses API client.

    Uses ``response_format=json_schema`` to force the model to emit a
    single JSON object with a ``markdown`` field. Compatible with
    ``o1``, ``o3``, ``gpt-4o`` and newer reasoning-capable models.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any = None,  # noqa: ANN401 — SDK client; concrete type is `openai.OpenAI`
    ) -> None:
        if client is not None:
            self._client = client
            return
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            msg = (
                "OpenAIReasoningClient requires OPENAI_API_KEY in the "
                "environment, an explicit `api_key=`, or an injected `client=`"
            )
            raise RuntimeError(msg)
        import openai  # noqa: PLC0415 — lazy SDK import keeps optional dependency out of cold path

        self._client = openai.OpenAI(api_key=key)

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: Stage,
        model: ReasoningModel,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str:
        if model.provider != "openai":
            msg = f"OpenAIReasoningClient cannot serve {model.provider} model"
            raise ValueError(msg)
        response = self._client.chat.completions.create(
            model=model.model_id,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_schema", "json_schema": _MARKDOWN_JSON_SCHEMA},
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        return _extract_openai_markdown(response, stage=stage)


def _extract_openai_markdown(response: Any, *, stage: Stage) -> str:  # noqa: ANN401
    """Pull the ``markdown`` field out of the JSON-schema-enforced response."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        msg = f"OpenAI stage-{stage} response had no choices"
        raise RuntimeError(msg)
    message = getattr(choices[0], "message", None)
    if message is None:
        msg = f"OpenAI stage-{stage} response choice missing message"
        raise RuntimeError(msg)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        msg = f"OpenAI stage-{stage} response content was empty"
        raise RuntimeError(msg)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        msg = f"OpenAI stage-{stage} response content was not valid JSON: {e}"
        raise RuntimeError(msg) from None
    md = payload.get("markdown") if isinstance(payload, dict) else None
    if not isinstance(md, str) or not md.strip():
        msg = f"OpenAI stage-{stage} response JSON missing `markdown` field"
        raise RuntimeError(msg)
    return md


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class DispatchReasoningClient:
    """Provider-routing wrapper.

    Holds one optional :class:`AnthropicReasoningClient` and one
    optional :class:`OpenAIReasoningClient`; ``emit_stage`` routes based
    on ``model.provider``. The orchestrator constructs this once at
    startup and reuses it across the entire run.
    """

    def __init__(
        self,
        *,
        anthropic_client: StageReasoningClient | None = None,
        openai_client: StageReasoningClient | None = None,
    ) -> None:
        self._anthropic = anthropic_client
        self._openai = openai_client

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: Stage,
        model: ReasoningModel,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str:
        client = self._client_for(model)
        return client.emit_stage(
            prompt=prompt,
            stage=stage,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _client_for(self, model: ReasoningModel) -> StageReasoningClient:
        if model.provider == "anthropic":
            if self._anthropic is None:
                msg = "no AnthropicReasoningClient configured; set ANTHROPIC_API_KEY"
                raise RuntimeError(msg)
            return self._anthropic
        if model.provider == "openai":
            if self._openai is None:
                msg = "no OpenAIReasoningClient configured; set OPENAI_API_KEY"
                raise RuntimeError(msg)
            return self._openai
        msg = f"unknown provider `{model.provider}`"
        raise ValueError(msg)


def build_dispatch_client(
    *,
    env: dict[str, str] | None = None,
) -> DispatchReasoningClient:
    """Construct a :class:`DispatchReasoningClient` wiring whichever
    providers are configured in the environment.

    At least one of ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` MUST be
    set; otherwise the loop has no reasoning model and the caller will
    have already failed at :func:`select_reasoning_model`. The function
    nonetheless raises if both are absent so a misconfigured dispatch
    surfaces early.
    """
    available = env if env is not None else os.environ
    anthropic_client: StageReasoningClient | None = None
    openai_client: StageReasoningClient | None = None
    if available.get("ANTHROPIC_API_KEY"):
        anthropic_client = AnthropicReasoningClient(api_key=available["ANTHROPIC_API_KEY"])
    if available.get("OPENAI_API_KEY"):
        openai_client = OpenAIReasoningClient(api_key=available["OPENAI_API_KEY"])
    if anthropic_client is None and openai_client is None:
        msg = (
            "build_dispatch_client requires at least one of ANTHROPIC_API_KEY "
            "or OPENAI_API_KEY in the environment"
        )
        raise RuntimeError(msg)
    return DispatchReasoningClient(
        anthropic_client=anthropic_client,
        openai_client=openai_client,
    )


__all__ = [
    "AnthropicReasoningClient",
    "DispatchReasoningClient",
    "OpenAIReasoningClient",
    "Stage",
    "StageReasoningClient",
    "build_dispatch_client",
]
