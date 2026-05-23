"""Structured decision log for the author dialog.

The DecisionRecord is the authoritative source of accepted clarifications
during an author run. It survives chat-history compaction, gives the
locked-in panel a clean projection to render, and gives the
repair-exhaustion control transfer a place to record the failure trail.

The on-disk format is JSONL at ``crates/<name>-strategy/.author/decisions.jsonl``.
One typed event per line, append-only. The LLM and the CLI both read the
record back via :meth:`DecisionRecord.project`, which replays events in
order and returns ``{field: current_value}`` last-write-wins.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

DecisionField = Literal[
    "crate_name",
    "universe",
    "mechanism_summary",
    "param_sketch",
    "smoke_spec",
    "experiment_spec",
    "edit_mode_target",
]

_DECISION_FIELDS: frozenset[str] = frozenset(
    [
        "crate_name",
        "universe",
        "mechanism_summary",
        "param_sketch",
        "smoke_spec",
        "experiment_spec",
        "edit_mode_target",
    ]
)


@dataclass(frozen=True)
class DialogStarted:
    """First event of a dialog. Records the optional seed and the model."""

    timestamp: str
    seed: str | None
    model: str
    schema_version: int = SCHEMA_VERSION
    event_type: Literal["dialog_started"] = "dialog_started"


@dataclass(frozen=True)
class DecisionLocked:
    """A clarification was accepted by the operator."""

    timestamp: str
    field: DecisionField
    value: Any
    schema_version: int = SCHEMA_VERSION
    event_type: Literal["decision_locked"] = "decision_locked"


@dataclass(frozen=True)
class DecisionAmended:
    """A previously-locked decision was revised."""

    timestamp: str
    field: DecisionField
    old_value: Any
    new_value: Any
    schema_version: int = SCHEMA_VERSION
    event_type: Literal["decision_amended"] = "decision_amended"


@dataclass(frozen=True)
class IntentFinalized:
    """The dialog handed the assembled intent to ``author_strategy``."""

    timestamp: str
    intent: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    event_type: Literal["intent_finalized"] = "intent_finalized"


@dataclass(frozen=True)
class RepairBudgetExhausted:
    """Control transferred back to the dialog after the repair loop gave up."""

    timestamp: str
    stage: str
    attempts: int
    last_feedback: str
    schema_version: int = SCHEMA_VERSION
    event_type: Literal["repair_budget_exhausted"] = "repair_budget_exhausted"


DecisionEvent = (
    DialogStarted | DecisionLocked | DecisionAmended | IntentFinalized | RepairBudgetExhausted
)

_EVENT_TYPES: dict[str, type[DecisionEvent]] = {
    "dialog_started": DialogStarted,
    "decision_locked": DecisionLocked,
    "decision_amended": DecisionAmended,
    "intent_finalized": IntentFinalized,
    "repair_budget_exhausted": RepairBudgetExhausted,
}


class DecisionRecordError(RuntimeError):
    """Raised when the on-disk decision log cannot be parsed."""


@dataclass
class DecisionRecord:
    """Append-only typed event log for an author dialog.

    Construct via :meth:`open` (creates parent dirs, no fsync on open) and
    feed events through :meth:`append`. :meth:`load` is a classmethod
    that yields every event on disk in order. :meth:`project` replays
    events into a current-value-per-field mapping.
    """

    path: Path
    _events: list[DecisionEvent] = field(default_factory=list)

    @classmethod
    def open(cls, path: Path) -> DecisionRecord:
        """Open (and create the parent dir for) a record at ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        record = cls(path=path)
        if path.exists():
            record._events = list(cls._iter_events(path))
        return record

    @classmethod
    def load(cls, path: Path) -> list[DecisionEvent]:
        """Read every event in the file in order."""
        if not path.exists():
            return []
        return list(cls._iter_events(path))

    def append(self, event: DecisionEvent) -> None:
        """Append ``event`` to the in-memory log and fsync it to disk."""
        is_decision = isinstance(event, DecisionLocked | DecisionAmended)
        if is_decision and event.field not in _DECISION_FIELDS:  # type: ignore[union-attr]
            msg = f"unknown decision field: {event.field!r}"  # type: ignore[union-attr]
            raise DecisionRecordError(msg)
        self._events.append(event)
        line = json.dumps(_event_to_dict(event), sort_keys=True, default=_json_default)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def events(self) -> list[DecisionEvent]:
        """Return the in-memory event list (newest last)."""
        return list(self._events)

    def project(self) -> dict[str, Any]:
        """Return ``{field: current_value}`` projected from events.

        Events are replayed in order; the most recent ``DecisionLocked``
        or ``DecisionAmended`` per field wins. Dialog-state events
        (``dialog_started``, ``intent_finalized``, ``repair_budget_exhausted``)
        are not projected.
        """
        current: dict[str, Any] = {}
        for ev in self._events:
            if isinstance(ev, DecisionLocked):
                current[ev.field] = ev.value
            elif isinstance(ev, DecisionAmended):
                current[ev.field] = ev.new_value
        return current

    @staticmethod
    def _iter_events(path: Path) -> list[DecisionEvent]:
        out: list[DecisionEvent] = []
        with path.open(encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError as e:
                    msg = f"{path}:{lineno}: invalid JSON: {e}"
                    raise DecisionRecordError(msg) from None
                out.append(_dict_to_event(data, path, lineno))
        return out


def _event_to_dict(event: DecisionEvent) -> dict[str, Any]:
    return asdict(event)


def _dict_to_event(data: dict[str, Any], path: Path, lineno: int) -> DecisionEvent:
    event_type = data.get("event_type")
    cls = _EVENT_TYPES.get(event_type) if isinstance(event_type, str) else None
    if cls is None:
        msg = f"{path}:{lineno}: unknown event_type {event_type!r}"
        raise DecisionRecordError(msg)
    payload = {k: v for k, v in data.items() if k != "event_type"}
    try:
        return cls(**payload)
    except TypeError as e:
        msg = f"{path}:{lineno}: cannot construct {cls.__name__}: {e}"
        raise DecisionRecordError(msg) from None


def _json_default(obj: object) -> object:
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    msg = f"object of type {type(obj).__name__} is not JSON-serializable"
    raise TypeError(msg)


def decision_record_path_for(crate_path: Path) -> Path:
    """Return the conventional decision-log path for a crate directory."""
    return crate_path / ".author" / "decisions.jsonl"


__all__ = [
    "SCHEMA_VERSION",
    "DecisionAmended",
    "DecisionEvent",
    "DecisionField",
    "DecisionLocked",
    "DecisionRecord",
    "DecisionRecordError",
    "DialogStarted",
    "IntentFinalized",
    "RepairBudgetExhausted",
    "decision_record_path_for",
]
