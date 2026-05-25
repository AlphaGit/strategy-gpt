"""ProgressBus: coalescing, heartbeat, boundary semantics."""

from __future__ import annotations

import asyncio

import pytest

from strategy_gpt.progress.bus import (
    COALESCE_WINDOW_SECS,
    HEARTBEAT_IDLE_THRESHOLD_SECS,
    ProgressBus,
)
from strategy_gpt.progress.events import (
    Heartbeat,
    PhaseBegin,
    PhaseEnd,
    PhaseProgress,
    PhaseStatus,
)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list = []

    def handle(self, ev) -> None:  # type: ignore[no-untyped-def]
        self.events.append(ev)

    def close(self) -> None:
        return


def test_begin_and_end_emit_through_sink() -> None:
    sink = _RecordingSink()
    bus = ProgressBus(sinks=[sink])
    bus.begin("p", total=10)
    bus.end("p", status=PhaseStatus.OK)
    assert [type(e) for e in sink.events] == [PhaseBegin, PhaseEnd]
    assert sink.events[1].status is PhaseStatus.OK


def test_tick_coalesces_within_window() -> None:
    sink = _RecordingSink()
    bus = ProgressBus(sinks=[sink])
    bus.begin("p", total=100)
    # First tick fires immediately.
    bus.tick("p", 1)
    # Rapid follow-ups within the window are coalesced.
    bus.tick("p", 2)
    bus.tick("p", 3)
    progress_events = [e for e in sink.events if isinstance(e, PhaseProgress)]
    assert len(progress_events) == 1
    assert progress_events[0].current == 1
    bus.flush()
    # Flush emits the highest pending value.
    progress_events = [e for e in sink.events if isinstance(e, PhaseProgress)]
    assert progress_events[-1].current == 3


def test_phase_end_is_never_dropped_even_within_window() -> None:
    sink = _RecordingSink()
    bus = ProgressBus(sinks=[sink])
    bus.begin("p")
    bus.tick("p", 1)
    bus.tick("p", 2)  # buffered
    bus.end("p")
    kinds = [type(e) for e in sink.events]
    # phase_end must appear; the buffered tick should have flushed first.
    assert PhaseEnd in kinds
    assert PhaseBegin in kinds


def test_cancel_all_open_synthesizes_phase_end() -> None:
    sink = _RecordingSink()
    bus = ProgressBus(sinks=[sink])
    bus.begin("a")
    bus.begin("b")
    bus.cancel_all_open(msg="ctrl-c")
    ends = [e for e in sink.events if isinstance(e, PhaseEnd)]
    paths = sorted(e.path for e in ends)
    assert paths == ["a", "b"]
    assert all(e.status is PhaseStatus.CANCELLED for e in ends)


def test_constants_match_spec() -> None:
    assert pytest.approx(0.25) == COALESCE_WINDOW_SECS
    assert pytest.approx(5.0) == HEARTBEAT_IDLE_THRESHOLD_SECS


@pytest.mark.asyncio
async def test_heartbeat_scanner_emits_for_idle_phase(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Shorten the heartbeat threshold for fast test cycle.
    import strategy_gpt.progress.bus as bus_mod  # noqa: PLC0415 — local rebind for monkeypatch

    monkeypatch.setattr(bus_mod, "HEARTBEAT_IDLE_THRESHOLD_SECS", 0.05)
    monkeypatch.setattr(bus_mod, "HEARTBEAT_SCAN_INTERVAL_SECS", 0.02)
    sink = _RecordingSink()
    bus = bus_mod.ProgressBus(sinks=[sink])
    bus.begin("idle")
    bus.start_heartbeat()
    await asyncio.sleep(0.15)
    await bus.stop_heartbeat()
    bus.end("idle")
    heartbeats = [e for e in sink.events if isinstance(e, Heartbeat)]
    assert heartbeats, "expected at least one heartbeat on the idle phase"
    assert heartbeats[0].path == "idle"
