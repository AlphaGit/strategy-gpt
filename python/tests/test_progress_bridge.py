"""StderrBridge: route progress lines, forward non-progress."""

from __future__ import annotations

import io
import json

from strategy_gpt.progress import ProgressBus
from strategy_gpt.progress.bridge import StderrBridge
from strategy_gpt.progress.events import PhaseBegin, PhaseEnd, PhaseProgress


class _Sink:
    def __init__(self) -> None:
        self.events: list = []

    def handle(self, ev) -> None:  # type: ignore[no-untyped-def]
        self.events.append(ev)

    def close(self) -> None:
        return


def test_progress_record_routes_to_bus() -> None:
    sink = _Sink()
    bus = ProgressBus(sinks=[sink])
    fwd = io.StringIO()
    bridge = StderrBridge(bus, forward_stream=fwd)
    line = json.dumps(
        {
            "target": "progress",
            "fields": {"kind": "phase_begin", "path": "worker.batch_0", "total": 5},
            "level": "INFO",
            "timestamp": "...",
        }
    )
    bridge(line)
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert isinstance(ev, PhaseBegin)
    assert ev.path == "worker.batch_0"
    assert ev.total == 5
    assert fwd.getvalue() == ""


def test_non_progress_record_forwards_unchanged() -> None:
    bus = ProgressBus(sinks=[_Sink()])
    fwd = io.StringIO()
    bridge = StderrBridge(bus, forward_stream=fwd)
    line = json.dumps({"target": "engine::coordinator", "level": "INFO", "message": "hi"})
    bridge(line)
    assert fwd.getvalue().strip() == line


def test_plain_text_line_forwards_to_stream() -> None:
    bus = ProgressBus(sinks=[_Sink()])
    fwd = io.StringIO()
    bridge = StderrBridge(bus, forward_stream=fwd)
    bridge("not json — plain text")
    assert "not json" in fwd.getvalue()


def test_malformed_progress_record_does_not_raise() -> None:
    sink = _Sink()
    bus = ProgressBus(sinks=[sink])
    fwd = io.StringIO()
    bridge = StderrBridge(bus, forward_stream=fwd)
    # Missing required `path` field.
    line = json.dumps({"target": "progress", "fields": {"kind": "phase_begin"}})
    bridge(line)  # Must not raise.
    assert sink.events == []


def test_flat_event_record_also_routes() -> None:
    sink = _Sink()
    bus = ProgressBus(sinks=[sink])
    bridge = StderrBridge(bus, forward_stream=io.StringIO())
    # Coordinator-direct shape — no `fields` envelope.
    line = json.dumps(
        {
            "target": "progress",
            "kind": "phase_end",
            "path": "worker.batch_0",
            "status": "ok",
            "wall_secs": 1.5,
        }
    )
    bridge(line)
    assert len(sink.events) == 1
    assert isinstance(sink.events[0], PhaseEnd)


def test_progress_tick_with_total_round_trip() -> None:
    sink = _Sink()
    bus = ProgressBus(sinks=[sink])
    bridge = StderrBridge(bus, forward_stream=io.StringIO())
    bridge(
        json.dumps(
            {
                "target": "progress",
                "fields": {
                    "kind": "phase_progress",
                    "path": "worker.batch_0.run_0.bars",
                    "current": 42,
                    "total": 100,
                },
            }
        )
    )
    assert isinstance(sink.events[0], PhaseProgress)
    assert sink.events[0].current == 42
    assert sink.events[0].total == 100
