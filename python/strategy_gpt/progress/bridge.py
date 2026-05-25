"""Bridge: parse worker stderr lines into ProgressEvents and forward the rest.

The Rust coordinator emits one line per stderr record from each worker
(plus its own coordinator-side progress records, written through the same
tracing layer in the orchestrator process). Each record is JSON shaped
like a `tracing` formatter output:

    {"timestamp": "...", "level": "INFO", "target": "progress",
     "fields": {"kind": "phase_begin", "path": "...", "total": 5000}}

Or the coordinator-side direct emission (no envelope, just the flat event).

`StderrBridge` is installed on `PyEngine.set_progress_callback`. Every
line crosses this bridge; lines tagged `target == "progress"` are
deserialized into a `ProgressEvent` and published on the `ProgressBus`;
every other line is forwarded verbatim to the orchestrator's structlog
stream (i.e. parent stderr) so RUST_LOG output remains visible.

Malformed progress records are dropped with a structured warning. The
bridge does not raise — the Rust side wraps every call and a raised
exception would only stall the worker stderr drain.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TextIO

import structlog

from .events import (
    UnknownEventKindError,
    event_from_dict,
)

if TYPE_CHECKING:
    from .bus import ProgressBus

_logger = structlog.get_logger(__name__)

_PROGRESS_TARGET = "progress"


class StderrBridge:
    """Callable that the Rust coordinator invokes per worker stderr line.

    Holds a reference to the orchestrator's ProgressBus and an output
    stream for forwarded non-progress lines (defaults to sys.stderr).
    """

    def __init__(
        self,
        bus: ProgressBus,
        *,
        forward_stream: TextIO | None = None,
    ) -> None:
        self._bus = bus
        self._forward: TextIO = forward_stream if forward_stream is not None else sys.stderr

    def __call__(self, line: str) -> None:  # noqa: PLR0911 — branchy guard chain
        """Receive one stderr line from a worker. Never raises."""
        line = line.strip()
        if not line:
            return
        # Cheap fast-path: only attempt JSON parse if the line looks like a
        # JSON object. Plain-text worker diagnostics (panic messages,
        # libc warnings) flow straight to the forward stream.
        if not line.startswith("{"):
            self._forward_line(line)
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            self._forward_line(line)
            return
        if not isinstance(record, Mapping):
            self._forward_line(line)
            return
        if record.get("target") != _PROGRESS_TARGET:
            self._forward_line(line)
            return
        flat = _flatten_tracing_record(record)
        try:
            event = event_from_dict(flat)
        except UnknownEventKindError as exc:
            _logger.warning("progress.bridge.unknown_kind", error=str(exc), raw=line)
            return
        except (KeyError, ValueError, TypeError) as exc:
            _logger.warning("progress.bridge.malformed", error=str(exc), raw=line)
            return
        try:
            self._bus.emit(event)
        except Exception as exc:
            _logger.warning("progress.bridge.emit_failed", error=str(exc))

    def _forward_line(self, line: str) -> None:
        try:
            self._forward.write(line + "\n")
            self._forward.flush()
        except (ValueError, OSError):
            return


def _flatten_tracing_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Lift a tracing JSON record's `fields` into a flat ProgressEvent dict.

    The tracing JSON formatter writes events as
    `{"target": "...", "fields": {...}, "level": "...", "timestamp": "..."}`.
    Our coordinator can also write flat records (no envelope). Handle both:
    when `fields` is a mapping, merge it; otherwise treat the record itself
    as the flat event.
    """
    fields = record.get("fields")
    if isinstance(fields, Mapping):
        flat: dict[str, Any] = {k: v for k, v in record.items() if k not in ("fields",)}
        flat.update(fields)
        # tracing inserts an empty "message" field; drop it.
        flat.pop("message", None)
        flat.pop("level", None)
        flat.pop("timestamp", None)
        flat.pop("target", None)
        # `emitted_at` is not supplied by tracing; derive from timestamp
        # via `time.monotonic()` is impossible (it's wall-clock from
        # tracing). The bus does not rely on it for ordering — it uses
        # arrival order. Set to 0.0 so the dataclass default is honored.
        flat.setdefault("emitted_at", 0.0)
        return flat
    # Already-flat record (coordinator direct emission path).
    flat = {k: v for k, v in record.items() if k not in ("level", "timestamp", "target")}
    flat.setdefault("emitted_at", 0.0)
    return flat


__all__ = ["StderrBridge"]
