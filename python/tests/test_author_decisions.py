"""Round-trip and projection tests for the DecisionRecord."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_gpt.author_decisions import (
    DecisionAmended,
    DecisionLocked,
    DecisionRecord,
    DecisionRecordError,
    DialogStarted,
    IntentFinalized,
    RepairBudgetExhausted,
    decision_record_path_for,
)


def _record_path(tmp_path: Path) -> Path:
    return decision_record_path_for(tmp_path / "spy-atr-strategy")


def test_round_trip_every_event_type(tmp_path: Path) -> None:
    """Append one of each event type, reload, assert structural equality."""
    path = _record_path(tmp_path)
    record = DecisionRecord.open(path)

    events = [
        DialogStarted(
            timestamp="2026-05-23T10:00:00Z",
            seed="trend-follow SPY",
            model="claude-opus-4-7",
        ),
        DecisionLocked(timestamp="2026-05-23T10:01:00Z", field="crate_name", value="spy-atr"),
        DecisionLocked(timestamp="2026-05-23T10:02:00Z", field="universe", value="SPY"),
        DecisionAmended(
            timestamp="2026-05-23T10:03:00Z",
            field="universe",
            old_value="SPY",
            new_value="SPY,QQQ",
        ),
        IntentFinalized(
            timestamp="2026-05-23T10:04:00Z",
            intent={"name": "spy-atr", "universe": "SPY,QQQ"},
        ),
        RepairBudgetExhausted(
            timestamp="2026-05-23T10:05:00Z",
            stage="emit",
            attempts=3,
            last_feedback="cargo build failed: borrow checker",
        ),
    ]
    for ev in events:
        record.append(ev)

    reloaded = DecisionRecord.load(path)
    assert reloaded == events


def test_projection_last_write_wins(tmp_path: Path) -> None:
    """``project`` returns last-write-wins per field, ignores non-decision events."""
    path = _record_path(tmp_path)
    record = DecisionRecord.open(path)

    record.append(
        DialogStarted(timestamp="2026-05-23T10:00:00Z", seed=None, model="claude-opus-4-7")
    )
    record.append(
        DecisionLocked(timestamp="2026-05-23T10:01:00Z", field="crate_name", value="spy-atr")
    )
    record.append(DecisionLocked(timestamp="2026-05-23T10:02:00Z", field="universe", value="SPY"))
    record.append(
        DecisionAmended(
            timestamp="2026-05-23T10:03:00Z",
            field="universe",
            old_value="SPY",
            new_value="SPY,QQQ",
        )
    )

    projection = record.project()
    assert projection == {"crate_name": "spy-atr", "universe": "SPY,QQQ"}


def test_open_recovers_existing_events(tmp_path: Path) -> None:
    """Reopening a path that already has events loads them into memory."""
    path = _record_path(tmp_path)
    first = DecisionRecord.open(path)
    first.append(DialogStarted(timestamp="2026-05-23T10:00:00Z", seed="seed", model="m"))
    first.append(DecisionLocked(timestamp="2026-05-23T10:01:00Z", field="crate_name", value="foo"))

    second = DecisionRecord.open(path)
    expected_event_count = 2
    assert len(second.events()) == expected_event_count
    assert second.project() == {"crate_name": "foo"}


def test_append_rejects_unknown_field(tmp_path: Path) -> None:
    """Decision events naming a field outside the canonical set raise."""
    path = _record_path(tmp_path)
    record = DecisionRecord.open(path)
    with pytest.raises(DecisionRecordError):
        record.append(
            DecisionLocked(
                timestamp="2026-05-23T10:00:00Z",
                field="bogus",
                value=1,
            )
        )


def test_load_invalid_json_raises(tmp_path: Path) -> None:
    """Malformed JSON in the file surfaces as ``DecisionRecordError``."""
    path = _record_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(DecisionRecordError):
        DecisionRecord.load(path)


def test_load_unknown_event_type_raises(tmp_path: Path) -> None:
    """An unknown ``event_type`` on a line raises ``DecisionRecordError``."""
    path = _record_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event_type": "ufo_sighting", "timestamp": "now"}\n', encoding="utf-8")
    with pytest.raises(DecisionRecordError):
        DecisionRecord.load(path)


def test_decision_record_path_for_layout(tmp_path: Path) -> None:
    """The conventional path lives under ``<crate>/.author/decisions.jsonl``."""
    crate = tmp_path / "crates" / "spy-atr-strategy"
    assert decision_record_path_for(crate) == crate / ".author" / "decisions.jsonl"
