"""Plain text sink: one human-readable line per phase begin/end.

Heartbeats throttled to one per 30 s per path. No ANSI escapes — safe
for `tee`-into-a-file workflows.
"""

from __future__ import annotations

import sys
from typing import TextIO

from ..events import Heartbeat, PhaseBegin, PhaseEnd, PhaseProgress, ProgressEvent

_HEARTBEAT_THROTTLE_SECS = 30.0


def _format_metrics(metrics: object) -> str:
    if not metrics:
        return ""
    if not isinstance(metrics, dict):
        return ""
    parts = [f"{k}={v:.4g}" for k, v in metrics.items() if isinstance(v, (int, float))]
    return (" {" + ", ".join(parts) + "}") if parts else ""


class PlainSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stderr
        self._last_heartbeat: dict[str, float | None] = {}

    def handle(self, event: ProgressEvent) -> None:
        if isinstance(event, PhaseBegin):
            total = f" (total={event.total})" if event.total is not None else ""
            msg = f" {event.msg}" if event.msg else ""
            self._write(f"[begin]   {event.path}{total}{msg}")
        elif isinstance(event, PhaseEnd):
            wall = f" wall={event.wall_secs:.2f}s"
            msg = f" {event.msg}" if event.msg else ""
            metrics = _format_metrics(event.metrics)
            self._write(f"[end]     {event.path} status={event.status.value}{wall}{msg}{metrics}")
        elif isinstance(event, PhaseProgress):
            # Plain sink suppresses per-tick progress; users wanting full
            # stream should use --progress=json. We only emit progress
            # lines when an explicit `msg` is provided (e.g. reasoning
            # summary), to keep tee'd logs readable.
            if event.msg:
                self._write(f"[tick]    {event.path} {event.msg}")
        elif isinstance(event, Heartbeat):
            last = self._last_heartbeat.get(event.path)
            if last is not None and event.emitted_at - last < _HEARTBEAT_THROTTLE_SECS:
                return
            self._last_heartbeat[event.path] = event.emitted_at
            self._write(
                f"[hb]      {event.path} wall={event.wall_secs:.1f}s "
                f"idle={event.since_last_event_secs:.1f}s"
            )

    def close(self) -> None:
        try:
            self._stream.flush()
        except (ValueError, OSError):
            return

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()


__all__ = ["PlainSink"]
