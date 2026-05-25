"""Typed progress event vocabulary.

Four event kinds keyed by a dotted `path` and a monotonic source clock.
The vocabulary is the only contract between emitters (orchestrator,
Rust workers) and sinks (renderers, JSONL writer).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class EventKind(StrEnum):
    PHASE_BEGIN = "phase_begin"
    PHASE_PROGRESS = "phase_progress"
    PHASE_END = "phase_end"
    HEARTBEAT = "heartbeat"


class PhaseStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PhaseBegin:
    kind: Literal[EventKind.PHASE_BEGIN] = field(default=EventKind.PHASE_BEGIN, init=False)
    path: str = ""
    emitted_at: float = 0.0
    started_at: float = 0.0
    total: int | None = None
    unit: str | None = None
    msg: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseProgress:
    kind: Literal[EventKind.PHASE_PROGRESS] = field(default=EventKind.PHASE_PROGRESS, init=False)
    path: str = ""
    emitted_at: float = 0.0
    current: int = 0
    total: int | None = None
    msg: str | None = None
    metrics: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True)
class PhaseEnd:
    kind: Literal[EventKind.PHASE_END] = field(default=EventKind.PHASE_END, init=False)
    path: str = ""
    emitted_at: float = 0.0
    status: PhaseStatus = PhaseStatus.OK
    wall_secs: float = 0.0
    msg: str | None = None
    metrics: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True)
class Heartbeat:
    kind: Literal[EventKind.HEARTBEAT] = field(default=EventKind.HEARTBEAT, init=False)
    path: str = ""
    emitted_at: float = 0.0
    wall_secs: float = 0.0
    since_last_event_secs: float = 0.0
    msg: str | None = None


ProgressEvent = PhaseBegin | PhaseProgress | PhaseEnd | Heartbeat


def event_to_dict(event: ProgressEvent) -> dict[str, Any]:  # noqa: PLR0912 — one branch per kind/field
    """Serialize an event to a JSON-friendly dict. Drops None-valued optionals."""
    out: dict[str, Any] = {"kind": event.kind.value, "path": event.path}
    if isinstance(event, PhaseBegin):
        out["emitted_at"] = event.emitted_at
        out["started_at"] = event.started_at
        if event.total is not None:
            out["total"] = event.total
        if event.unit is not None:
            out["unit"] = event.unit
        if event.msg is not None:
            out["msg"] = event.msg
    elif isinstance(event, PhaseProgress):
        out["emitted_at"] = event.emitted_at
        out["current"] = event.current
        if event.total is not None:
            out["total"] = event.total
        if event.msg is not None:
            out["msg"] = event.msg
        if event.metrics:
            out["metrics"] = dict(event.metrics)
    elif isinstance(event, PhaseEnd):
        out["emitted_at"] = event.emitted_at
        out["status"] = event.status.value
        out["wall_secs"] = event.wall_secs
        if event.msg is not None:
            out["msg"] = event.msg
        if event.metrics:
            out["metrics"] = dict(event.metrics)
    elif isinstance(event, Heartbeat):
        out["emitted_at"] = event.emitted_at
        out["wall_secs"] = event.wall_secs
        out["since_last_event_secs"] = event.since_last_event_secs
        if event.msg is not None:
            out["msg"] = event.msg
    return out


class UnknownEventKindError(ValueError):
    """Raised when a record's `kind` field is not one of the four defined kinds."""


def event_from_dict(record: Mapping[str, Any]) -> ProgressEvent:
    """Deserialize a dict (e.g. from a JSON line) into a ProgressEvent.

    Raises UnknownEventKindError if `kind` is missing or unrecognised.
    Raises (KeyError, ValueError, TypeError) for malformed required fields.
    """
    kind_raw = record.get("kind")
    if not isinstance(kind_raw, str):
        raise UnknownEventKindError(f"missing or non-string kind: {kind_raw!r}")
    try:
        kind = EventKind(kind_raw)
    except ValueError as exc:
        raise UnknownEventKindError(f"unknown event kind: {kind_raw!r}") from exc

    path = record.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"missing or invalid path: {path!r}")
    emitted_at = float(record.get("emitted_at", 0.0))

    if kind is EventKind.PHASE_BEGIN:
        return PhaseBegin(
            path=path,
            emitted_at=emitted_at,
            started_at=float(record.get("started_at", emitted_at)),
            total=_opt_int(record.get("total")),
            unit=_opt_str(record.get("unit")),
            msg=_opt_str(record.get("msg")),
        )
    if kind is EventKind.PHASE_PROGRESS:
        return PhaseProgress(
            path=path,
            emitted_at=emitted_at,
            current=int(record.get("current", 0)),
            total=_opt_int(record.get("total")),
            msg=_opt_str(record.get("msg")),
            metrics=_opt_metrics(record.get("metrics")),
        )
    if kind is EventKind.PHASE_END:
        return PhaseEnd(
            path=path,
            emitted_at=emitted_at,
            status=PhaseStatus(record.get("status", "ok")),
            wall_secs=float(record.get("wall_secs", 0.0)),
            msg=_opt_str(record.get("msg")),
            metrics=_opt_metrics(record.get("metrics")),
        )
    # kind is EventKind.HEARTBEAT
    return Heartbeat(
        path=path,
        emitted_at=emitted_at,
        wall_secs=float(record.get("wall_secs", 0.0)),
        since_last_event_secs=float(record.get("since_last_event_secs", 0.0)),
        msg=_opt_str(record.get("msg")),
    )


def _opt_int(v: Any) -> int | None:  # noqa: ANN401 — wire input is JSON-typed `Any`
    if v is None:
        return None
    return int(v)


def _opt_str(v: Any) -> str | None:  # noqa: ANN401 — wire input is JSON-typed `Any`
    if v is None:
        return None
    return str(v)


def _opt_metrics(v: Any) -> Mapping[str, float] | None:  # noqa: ANN401 — wire input is JSON-typed
    if v is None:
        return None
    if not isinstance(v, Mapping):
        raise TypeError(f"metrics must be a mapping, got {type(v).__name__}")
    return {str(k): float(val) for k, val in v.items()}


__all__ = [
    "EventKind",
    "Heartbeat",
    "PhaseBegin",
    "PhaseEnd",
    "PhaseProgress",
    "PhaseStatus",
    "ProgressEvent",
    "UnknownEventKindError",
    "event_from_dict",
    "event_to_dict",
]
