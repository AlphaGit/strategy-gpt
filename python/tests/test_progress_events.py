"""ProgressEvent serialization round-trip tests."""

from __future__ import annotations

import json

import pytest

from strategy_gpt.progress.events import (
    EventKind,
    Heartbeat,
    PhaseBegin,
    PhaseEnd,
    PhaseProgress,
    PhaseStatus,
    UnknownEventKindError,
    event_from_dict,
    event_to_dict,
)


def test_phase_begin_round_trip() -> None:
    ev = PhaseBegin(path="optimize", emitted_at=1.0, started_at=1.0, total=10, unit="trials")
    d = event_to_dict(ev)
    assert d["kind"] == "phase_begin"
    assert d["path"] == "optimize"
    assert d["total"] == 10
    rt = event_from_dict(d)
    assert isinstance(rt, PhaseBegin)
    assert rt.path == "optimize"
    assert rt.total == 10
    assert rt.unit == "trials"


def test_phase_progress_round_trip_with_metrics() -> None:
    ev = PhaseProgress(
        path="optimize.fold_2.trial_47",
        emitted_at=2.5,
        current=47,
        total=200,
        msg="trial #47",
        metrics={"sharpe": 1.41, "best": 1.41},
    )
    d = event_to_dict(ev)
    rt = event_from_dict(d)
    assert isinstance(rt, PhaseProgress)
    assert rt.current == 47
    assert rt.total == 200
    assert rt.metrics == {"sharpe": pytest.approx(1.41), "best": pytest.approx(1.41)}


def test_phase_end_serialization() -> None:
    ev = PhaseEnd(path="worker.batch_3.run_0", status=PhaseStatus.OK, wall_secs=12.34)
    d = event_to_dict(ev)
    assert d["status"] == "ok"
    assert d["wall_secs"] == pytest.approx(12.34)
    rt = event_from_dict(d)
    assert isinstance(rt, PhaseEnd)
    assert rt.status is PhaseStatus.OK


def test_heartbeat_round_trip() -> None:
    ev = Heartbeat(
        path="hypothesize.generate", emitted_at=5.0, wall_secs=6.0, since_last_event_secs=5.5
    )
    d = event_to_dict(ev)
    assert d["kind"] == "heartbeat"
    rt = event_from_dict(d)
    assert isinstance(rt, Heartbeat)
    assert rt.since_last_event_secs == pytest.approx(5.5)


def test_unknown_kind_raises() -> None:
    with pytest.raises(UnknownEventKindError):
        event_from_dict({"kind": "phase_unknown", "path": "x"})


def test_jsonl_payload_is_compact() -> None:
    ev = PhaseProgress(path="p", emitted_at=1.0, current=1)
    line = json.dumps(event_to_dict(ev), separators=(",", ":"))
    assert line == '{"kind":"phase_progress","path":"p","emitted_at":1.0,"current":1}'


@pytest.mark.parametrize(
    "kind_value",
    [k.value for k in EventKind],
)
def test_every_kind_round_trips_through_event_kind_enum(kind_value: str) -> None:
    # `EventKind(kind_value)` must succeed for every defined value.
    assert EventKind(kind_value).value == kind_value
