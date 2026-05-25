"""ProgressBus: in-process fan-out of ProgressEvents to one or more sinks.

Source-side: hot loops call `tick(path, current)` which coalesces repeat
ticks within a 250 ms window per path (keeping the highest `current`).
phase_begin / phase_end / heartbeat are never coalesced.

Heartbeat: a background asyncio task scans open phases every second and
synthesizes a Heartbeat for any phase silent for >= 5 s. Suppressed when
a PhaseEnd fires in the same tick.

The bus is not thread-safe across threads, but emit() can be called from
any asyncio task in the same loop. For cross-process events (Rust
workers) use the bridge to deserialize and call emit() on the
orchestrator loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .events import (
    Heartbeat,
    PhaseBegin,
    PhaseEnd,
    PhaseProgress,
    PhaseStatus,
    ProgressEvent,
)
from .sinks.base import ProgressSink

COALESCE_WINDOW_SECS = 0.25
HEARTBEAT_SCAN_INTERVAL_SECS = 1.0
HEARTBEAT_IDLE_THRESHOLD_SECS = 5.0


@dataclass(slots=True)
class _OpenPhase:
    path: str
    started_at: float
    last_event_at: float
    last_tick_current: int = 0
    last_tick_emitted_at: float = 0.0


class ProgressBus:
    def __init__(self, sinks: Iterable[ProgressSink] | None = None) -> None:
        self._sinks: list[ProgressSink] = list(sinks or [])
        self._open: dict[str, _OpenPhase] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False
        # Buffer for tick coalescing — latest pending ticks keyed by path.
        self._pending_tick: dict[str, PhaseProgress] = {}
        self._flush_lock = asyncio.Lock()

    def add_sink(self, sink: ProgressSink) -> None:
        self._sinks.append(sink)

    @property
    def sinks(self) -> tuple[ProgressSink, ...]:
        return tuple(self._sinks)

    @property
    def open_paths(self) -> tuple[str, ...]:
        return tuple(self._open.keys())

    def emit(self, event: ProgressEvent) -> None:
        """Deliver an event to every sink. Updates open-phase bookkeeping."""
        now_mono = time.monotonic()
        if isinstance(event, PhaseBegin):
            self._open[event.path] = _OpenPhase(
                path=event.path,
                started_at=event.started_at or event.emitted_at or now_mono,
                last_event_at=event.emitted_at or now_mono,
            )
        elif isinstance(event, PhaseProgress):
            phase = self._open.get(event.path)
            if phase is not None:
                phase.last_event_at = event.emitted_at or now_mono
                phase.last_tick_current = max(phase.last_tick_current, event.current)
                phase.last_tick_emitted_at = phase.last_event_at
        elif isinstance(event, PhaseEnd):
            # Flush any pending coalesced tick first so the final `current`
            # is observed before the end event fans out.
            pending = self._pending_tick.pop(event.path, None)
            if pending is not None:
                self._deliver(pending)
            self._open.pop(event.path, None)
        elif isinstance(event, Heartbeat):
            phase = self._open.get(event.path)
            if phase is not None:
                # Heartbeats do not reset the "last real activity" clock.
                pass
        self._deliver(event)

    def begin(
        self,
        path: str,
        *,
        total: int | None = None,
        unit: str | None = None,
        msg: str | None = None,
    ) -> None:
        now = time.monotonic()
        self.emit(
            PhaseBegin(
                path=path,
                emitted_at=now,
                started_at=now,
                total=total,
                unit=unit,
                msg=msg,
            )
        )

    def tick(
        self,
        path: str,
        current: int,
        *,
        total: int | None = None,
        msg: str | None = None,
        metrics: Mapping[str, float] | None = None,
    ) -> None:
        """Source-side coalescing emit. Replaces any pending tick for path.

        The latest pending tick is flushed when 250 ms elapses since the
        last delivered tick, or when phase_end fires, or when flush() is
        called.
        """
        now = time.monotonic()
        phase = self._open.get(path)
        event = PhaseProgress(
            path=path,
            emitted_at=now,
            current=current,
            total=total,
            msg=msg,
            metrics=metrics,
        )
        if phase is None:
            # No open phase tracked: deliver directly (cannot coalesce
            # without prior begin bookkeeping). Bridge-translated events
            # from workers may arrive before the orchestrator sees the
            # begin if PhaseBegin is lost; deliver to be safe.
            self._deliver(event)
            return
        if now - phase.last_tick_emitted_at < COALESCE_WINDOW_SECS:
            existing = self._pending_tick.get(path)
            if existing is None or current > existing.current:
                self._pending_tick[path] = event
            return
        self._pending_tick.pop(path, None)
        phase.last_event_at = now
        phase.last_tick_current = max(phase.last_tick_current, current)
        phase.last_tick_emitted_at = now
        self._deliver(event)

    def end(
        self,
        path: str,
        *,
        status: PhaseStatus = PhaseStatus.OK,
        msg: str | None = None,
        metrics: Mapping[str, float] | None = None,
    ) -> None:
        now = time.monotonic()
        phase = self._open.get(path)
        wall = (now - phase.started_at) if phase is not None else 0.0
        self.emit(
            PhaseEnd(
                path=path,
                emitted_at=now,
                status=status,
                wall_secs=wall,
                msg=msg,
                metrics=metrics,
            )
        )

    def flush(self) -> None:
        """Emit any pending coalesced ticks immediately."""
        if not self._pending_tick:
            return
        pending = list(self._pending_tick.values())
        self._pending_tick.clear()
        for event in pending:
            phase = self._open.get(event.path)
            if phase is not None:
                phase.last_event_at = event.emitted_at
                phase.last_tick_current = max(phase.last_tick_current, event.current)
                phase.last_tick_emitted_at = event.emitted_at
            self._deliver(event)

    def cancel_all_open(self, *, msg: str | None = None) -> None:
        """Emit phase_end(status=cancelled) for every still-open phase.

        Iteration order matches insertion (most recent last) so the rendered
        final state reflects unwinding the phase stack.
        """
        for path in tuple(self._open.keys()):
            self.end(path, status=PhaseStatus.CANCELLED, msg=msg)

    def _deliver(self, event: ProgressEvent) -> None:
        for sink in self._sinks:
            try:
                sink.handle(event)
            except Exception:  # noqa: S112 — UX sink failures must not poison emit
                # Sinks are UX-only; an exception in one must not break
                # event delivery to others or to the call site.
                continue

    def start_heartbeat(self) -> None:
        """Spawn the heartbeat scanner on the current asyncio loop."""
        if self._heartbeat_task is not None:
            return
        loop = asyncio.get_event_loop()
        self._heartbeat_task = loop.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            return
        task = self._heartbeat_task
        self._heartbeat_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(HEARTBEAT_SCAN_INTERVAL_SECS)
                self._scan_heartbeats()
        except asyncio.CancelledError:
            return

    def _scan_heartbeats(self) -> None:
        now = time.monotonic()
        for path, phase in list(self._open.items()):
            idle = now - phase.last_event_at
            if idle < HEARTBEAT_IDLE_THRESHOLD_SECS:
                continue
            wall = now - phase.started_at
            event = Heartbeat(
                path=path,
                emitted_at=now,
                wall_secs=wall,
                since_last_event_secs=idle,
            )
            phase.last_event_at = now
            self._deliver(event)

    def close(self) -> None:
        self._closed = True
        for sink in self._sinks:
            with contextlib.suppress(Exception):
                sink.close()


__all__ = ["COALESCE_WINDOW_SECS", "HEARTBEAT_IDLE_THRESHOLD_SECS", "ProgressBus"]
