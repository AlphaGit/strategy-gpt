"""Compaction-resilience tests for ``run_intent_dialog``.

The dialog must keep its locked-in decisions in a structured record on
disk, not just in the LLM's chat history. These tests stub the
reasoning client to inspect what the next prompt actually contains
after a simulated history compaction.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from strategy_gpt.author import DialogError, run_intent_dialog
from strategy_gpt.author_decisions import (
    DecisionLocked,
    DecisionRecord,
    decision_record_path_for,
)


class _ScriptedClient:
    """LLM stub returning canned responses and capturing every transcript."""

    def __init__(self, responses: Iterator[str]) -> None:
        self._responses = responses
        self.captured_transcripts: list[list[dict[str, str]]] = []

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        del system
        self.captured_transcripts.append([dict(m) for m in transcript])
        return next(self._responses)

    def emit_files(self, *, system: str, user: str) -> str:
        del system, user
        msg = "emit_files unused in dialog-only tests"
        raise NotImplementedError(msg)


def _turn_with_decisions(decisions_yaml: str, prose: str = "Question?") -> str:
    return f"# DecisionsSoFar\n```yaml\n{decisions_yaml.strip()}\n```\n\n{prose}\n"


_FINAL_INTENT = textwrap.dedent(
    """\
    # DecisionsSoFar
    ```yaml
    crate_name: spy-atr
    universe: SPY
    mechanism_summary: |
      ATR-based trend following
    param_sketch:
      params: []
    smoke_spec:
      symbol: SPY
      resolution: 1d
      start: 2024-01-01
      end: 2024-04-01
      provider: yfinance
    ```

    # AuthorIntent
    ```yaml
    name: spy-atr
    description: |
      SPY ATR trend following.
    mechanism_summary: |
      ATR-based trend following.
    param_schema_sketch:
      params: []
    smoke_spec:
      symbol: SPY
      resolution: 1d
      start: 2024-01-01
      end: 2024-04-01
      provider: yfinance
    ```
    """
)


def test_decisions_persist_to_disk_each_turn(tmp_path: Path) -> None:
    """Every accepted decision in a turn lands as an event in the record."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()

    captured: list[DecisionRecord] = []
    responses = iter(
        [
            _turn_with_decisions("crate_name: spy-atr"),
            _turn_with_decisions(
                "crate_name: spy-atr\nuniverse: SPY",
                prose="What stops?",
            ),
            _FINAL_INTENT,
        ]
    )
    client = _ScriptedClient(responses)
    replies = iter(["SPY only", "ATR stops"])

    intent = run_intent_dialog(
        seed="trend-follow SPY",
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: next(replies),
        write_user=lambda _: None,
        on_record_ready=captured.append,
    )

    assert intent.name == "spy-atr"
    assert len(captured) == 1
    record_path = decision_record_path_for(crates_dir / "spy-atr-strategy")
    events = DecisionRecord.load(record_path)
    locked_fields = [
        e.field for e in events if isinstance(e, DecisionLocked)  # type: ignore[attr-defined]
    ]
    assert "crate_name" in locked_fields
    assert "universe" in locked_fields
    assert "mechanism_summary" in locked_fields
    assert "smoke_spec" in locked_fields
    # IntentFinalized is the last event
    assert events[-1].event_type == "intent_finalized"  # type: ignore[union-attr]


def test_compaction_resilient_next_prompt_carries_decisions(tmp_path: Path) -> None:
    """Even when the LLM's chat history is short, the next user turn carries the projection."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()

    responses = iter(
        [
            _turn_with_decisions("crate_name: spy-atr"),
            _turn_with_decisions("crate_name: spy-atr\nuniverse: SPY"),
            _FINAL_INTENT,
        ]
    )
    client = _ScriptedClient(responses)
    replies = iter(["SPY only", "ATR stops"])

    run_intent_dialog(
        seed="trend-follow SPY",
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: next(replies),
        write_user=lambda _: None,
    )

    # The third turn (i.e., transcript captured before turn 3) must carry
    # the decisions projection in its most recent user message.
    third_transcript = client.captured_transcripts[2]
    last_user_msg = next(m for m in reversed(third_transcript) if m["role"] == "user")
    assert "DecisionsSoFar" in last_user_msg["content"]
    assert "crate_name: spy-atr" in last_user_msg["content"]
    assert "universe: SPY" in last_user_msg["content"]


def test_amendment_emitted_when_field_value_changes(tmp_path: Path) -> None:
    """A decision changing value across turns yields a DecisionAmended event."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()

    responses = iter(
        [
            _turn_with_decisions("crate_name: spy-atr\nuniverse: SPY"),
            _turn_with_decisions(
                "crate_name: spy-atr\nuniverse: SPY,QQQ",
                prose="OK, broadening; next?",
            ),
            _FINAL_INTENT,
        ]
    )
    client = _ScriptedClient(responses)
    replies = iter(["broaden to SPY+QQQ", "go ahead"])

    run_intent_dialog(
        seed="trend-follow SPY",
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: next(replies),
        write_user=lambda _: None,
    )

    record_path = decision_record_path_for(crates_dir / "spy-atr-strategy")
    events = DecisionRecord.load(record_path)
    kinds = [e.event_type for e in events]  # type: ignore[union-attr]
    assert "decision_amended" in kinds
    amendments = [e for e in events if e.event_type == "decision_amended"]  # type: ignore[union-attr]
    assert any(
        e.field == "universe" and e.old_value == "SPY" and e.new_value == "SPY,QQQ"  # type: ignore[attr-defined]
        for e in amendments
    )


def test_dialog_without_decisions_block_still_works(tmp_path: Path) -> None:
    """Backwards-compat: if the LLM never emits ``# DecisionsSoFar``, no record opens."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()

    responses = iter(
        [
            "What instrument?",
            textwrap.dedent(
                """\
                # AuthorIntent
                ```yaml
                name: spy-atr
                description: |
                  d
                mechanism_summary: |
                  m
                param_schema_sketch:
                  params: []
                smoke_spec:
                  symbol: SPY
                  resolution: 1d
                  start: 2024-01-01
                  end: 2024-04-01
                  provider: yfinance
                ```
                """
            ),
        ]
    )
    client = _ScriptedClient(responses)
    replies = iter(["SPY"])

    intent = run_intent_dialog(
        seed=None,
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: next(replies),
        write_user=lambda _: None,
    )

    assert intent.name == "spy-atr"
    assert not (crates_dir / "spy-atr-strategy" / ".author").exists()


def test_max_turns_exceeded_raises(tmp_path: Path) -> None:
    """The dialog still bounces off ``max_turns`` when the LLM never finalizes."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()

    responses = iter([_turn_with_decisions("crate_name: spy-atr")] * 10)
    client = _ScriptedClient(responses)
    replies = iter(["ok"] * 10)

    with pytest.raises(DialogError):
        run_intent_dialog(
            seed="seed",
            reasoning_client=client,
            crates_dir=crates_dir,
            ask_user=lambda _: next(replies),
            write_user=lambda _: None,
            max_turns=2,
        )
