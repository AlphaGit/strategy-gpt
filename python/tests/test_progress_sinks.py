"""Sink behavior tests."""

from __future__ import annotations

import io
import json

from strategy_gpt.progress.events import (
    Heartbeat,
    PhaseBegin,
    PhaseEnd,
    PhaseProgress,
    PhaseStatus,
)
from strategy_gpt.progress.sinks.base import NullSink
from strategy_gpt.progress.sinks.jsonl import JsonlSink
from strategy_gpt.progress.sinks.plain import PlainSink
from strategy_gpt.progress.sinks.resolver import ProgressMode, resolve_sink


def test_jsonl_sink_writes_one_line_per_event() -> None:
    stream = io.StringIO()
    sink = JsonlSink(stream=stream)
    sink.handle(PhaseBegin(path="p", emitted_at=1.0, started_at=1.0))
    sink.handle(PhaseEnd(path="p", emitted_at=2.0, status=PhaseStatus.OK, wall_secs=1.0))
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "phase_begin"
    assert first["path"] == "p"


def test_plain_sink_suppresses_unmsg_progress_events() -> None:
    stream = io.StringIO()
    sink = PlainSink(stream=stream)
    sink.handle(PhaseBegin(path="p", emitted_at=0.0))
    sink.handle(PhaseProgress(path="p", emitted_at=0.1, current=1))  # no msg → suppressed
    sink.handle(PhaseEnd(path="p", emitted_at=1.0, status=PhaseStatus.OK, wall_secs=1.0))
    lines = stream.getvalue().splitlines()
    # Begin + End lines, but no tick line.
    assert any("begin" in line for line in lines)
    assert any("end" in line for line in lines)
    assert not any("tick" in line for line in lines)


def test_plain_sink_throttles_heartbeats() -> None:
    stream = io.StringIO()
    sink = PlainSink(stream=stream)
    sink.handle(Heartbeat(path="p", emitted_at=0.0, wall_secs=5.0, since_last_event_secs=5.0))
    sink.handle(Heartbeat(path="p", emitted_at=10.0, wall_secs=15.0, since_last_event_secs=5.0))
    sink.handle(Heartbeat(path="p", emitted_at=40.0, wall_secs=45.0, since_last_event_secs=5.0))
    hb_lines = [line for line in stream.getvalue().splitlines() if "[hb]" in line]
    # Only the first and the third (>30s after first) survive.
    assert len(hb_lines) == 2


def test_resolve_sink_off_installs_no_sink() -> None:
    assert resolve_sink(ProgressMode.OFF) == []


def test_resolve_sink_json_installs_jsonl() -> None:
    sinks = resolve_sink(ProgressMode.JSON, stderr=io.StringIO())
    assert len(sinks) == 1
    assert sinks[0].__class__.__name__ == "JsonlSink"


def test_resolve_sink_auto_pipe_picks_jsonl() -> None:
    sinks = resolve_sink(ProgressMode.AUTO, stderr=io.StringIO(), isatty_override=False)
    assert sinks[0].__class__.__name__ == "JsonlSink"


def test_resolve_sink_auto_tty_picks_rich() -> None:
    sinks = resolve_sink(ProgressMode.AUTO, stderr=io.StringIO(), isatty_override=True)
    assert sinks[0].__class__.__name__ == "RichLiveSink"


def test_null_sink_does_not_raise_on_close() -> None:
    sink = NullSink()
    sink.handle(PhaseBegin(path="p", emitted_at=0.0))
    sink.close()
