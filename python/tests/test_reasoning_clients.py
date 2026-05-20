"""Tests for the Anthropic / OpenAI reasoning clients and the dispatch layer.

All network I/O is stubbed via injected client objects so the tests run
offline. The Anthropic test stubs the SDK's `messages.create` to return
a content list containing a forced `tool_use` block; the OpenAI test
stubs the chat-completions surface with a JSON-encoded `markdown`
payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from strategy_gpt.prompts import StagePrompt
from strategy_gpt.reasoning import ReasoningModel
from strategy_gpt.reasoning_clients import (
    AnthropicReasoningClient,
    DispatchReasoningClient,
    OpenAIReasoningClient,
    build_dispatch_client,
)

# ---------- Anthropic ----------


@dataclass
class _AnthropicBlock:
    type: str
    input: dict[str, Any]


@dataclass
class _AnthropicResponse:
    content: list[_AnthropicBlock]


class _StubAnthropicClient:
    def __init__(self, response: _AnthropicResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _AnthropicResponse:
        self.calls.append(kwargs)
        return self.response


def test_anthropic_extracts_markdown_from_tool_use() -> None:
    stub = _StubAnthropicClient(
        _AnthropicResponse(
            content=[
                _AnthropicBlock(type="tool_use", input={"markdown": "# Idea\n\nx\n"}),
            ]
        ),
    )
    client = AnthropicReasoningClient(client=stub)
    text = client.emit_stage(
        prompt=StagePrompt(system="sys", user="usr"),
        stage=1,
        model=ReasoningModel(provider="anthropic", model_id="claude-opus-4-7"),
    )
    assert text == "# Idea\n\nx\n"
    assert stub.calls[0]["model"] == "claude-opus-4-7"
    assert stub.calls[0]["tool_choice"] == {"type": "tool", "name": "emit_markdown"}


def test_anthropic_rejects_wrong_provider() -> None:
    stub = _StubAnthropicClient(_AnthropicResponse(content=[]))
    client = AnthropicReasoningClient(client=stub)
    with pytest.raises(ValueError, match="cannot serve openai"):
        client.emit_stage(
            prompt=StagePrompt(system="", user=""),
            stage=1,
            model=ReasoningModel(provider="openai", model_id="o3"),
        )


def test_anthropic_raises_when_no_tool_use_block() -> None:
    stub = _StubAnthropicClient(_AnthropicResponse(content=[]))
    client = AnthropicReasoningClient(client=stub)
    with pytest.raises(RuntimeError, match="did not contain an emit_markdown"):
        client.emit_stage(
            prompt=StagePrompt(system="", user=""),
            stage=1,
            model=ReasoningModel(provider="anthropic", model_id="claude-opus-4-7"),
        )


def test_anthropic_requires_key_or_client() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicReasoningClient(api_key=None)


# ---------- OpenAI ----------


@dataclass
class _OpenAIMessage:
    content: str


@dataclass
class _OpenAIChoice:
    message: _OpenAIMessage


@dataclass
class _OpenAIResponse:
    choices: list[_OpenAIChoice]


class _StubOpenAIChat:
    def __init__(self, response: _OpenAIResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _OpenAIResponse:
        self.calls.append(kwargs)
        return self.response


class _StubOpenAIClient:
    def __init__(self, response: _OpenAIResponse) -> None:
        self.completions = _StubOpenAIChat(response)
        self.chat = self  # so client.chat.completions.create works


def test_openai_extracts_markdown_from_json_schema() -> None:
    payload = json.dumps({"markdown": "## Cargo.toml\n```\n[package]\n```\n"})
    stub = _StubOpenAIClient(
        _OpenAIResponse(choices=[_OpenAIChoice(message=_OpenAIMessage(content=payload))]),
    )
    client = OpenAIReasoningClient(client=stub)
    text = client.emit_stage(
        prompt=StagePrompt(system="sys", user="usr"),
        stage=3,
        model=ReasoningModel(provider="openai", model_id="o3"),
    )
    assert "## Cargo.toml" in text
    call = stub.completions.calls[0]
    assert call["model"] == "o3"
    assert call["response_format"]["type"] == "json_schema"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"


def test_openai_raises_on_invalid_json() -> None:
    stub = _StubOpenAIClient(
        _OpenAIResponse(choices=[_OpenAIChoice(message=_OpenAIMessage(content="not-json"))]),
    )
    client = OpenAIReasoningClient(client=stub)
    with pytest.raises(RuntimeError, match="not valid JSON"):
        client.emit_stage(
            prompt=StagePrompt(system="", user=""),
            stage=1,
            model=ReasoningModel(provider="openai", model_id="o3"),
        )


def test_openai_raises_when_markdown_field_missing() -> None:
    stub = _StubOpenAIClient(
        _OpenAIResponse(choices=[_OpenAIChoice(message=_OpenAIMessage(content="{}"))]),
    )
    client = OpenAIReasoningClient(client=stub)
    with pytest.raises(RuntimeError, match="missing `markdown`"):
        client.emit_stage(
            prompt=StagePrompt(system="", user=""),
            stage=1,
            model=ReasoningModel(provider="openai", model_id="o3"),
        )


def test_openai_requires_key_or_client() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIReasoningClient(api_key=None)


# ---------- Dispatch ----------


class _RecordingClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[ReasoningModel] = []

    def emit_stage(
        self,
        *,
        prompt: StagePrompt,
        stage: int,
        model: ReasoningModel,
        **_: Any,
    ) -> str:
        self.calls.append(model)
        return f"{self.name}:stage={stage}"


def test_dispatch_routes_by_provider() -> None:
    a = _RecordingClient("anthropic")
    o = _RecordingClient("openai")
    dispatch = DispatchReasoningClient(anthropic_client=a, openai_client=o)
    out_a = dispatch.emit_stage(
        prompt=StagePrompt(system="", user=""),
        stage=1,
        model=ReasoningModel(provider="anthropic", model_id="m1"),
    )
    out_o = dispatch.emit_stage(
        prompt=StagePrompt(system="", user=""),
        stage=2,
        model=ReasoningModel(provider="openai", model_id="m2"),
    )
    assert out_a == "anthropic:stage=1"
    assert out_o == "openai:stage=2"
    assert a.calls == [ReasoningModel(provider="anthropic", model_id="m1")]
    assert o.calls == [ReasoningModel(provider="openai", model_id="m2")]


def test_dispatch_raises_when_provider_unconfigured() -> None:
    dispatch = DispatchReasoningClient(anthropic_client=_RecordingClient("a"), openai_client=None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        dispatch.emit_stage(
            prompt=StagePrompt(system="", user=""),
            stage=1,
            model=ReasoningModel(provider="openai", model_id="o3"),
        )


def test_build_dispatch_client_requires_env_key() -> None:
    with pytest.raises(RuntimeError, match="at least one of"):
        build_dispatch_client(env={})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
