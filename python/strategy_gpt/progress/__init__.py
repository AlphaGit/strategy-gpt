"""Progress reporting: typed phase-tree events, an async bus, and pluggable sinks.

This package is the UX channel for long-running CLI commands. It never writes
to the experiment ledger and never alters command results on stdout. Events
flow exclusively to stderr through one selected sink (rich live renderer,
plain text, JSONL, or off).
"""

from __future__ import annotations

from .bridge import StderrBridge
from .bus import ProgressBus
from .context import begin_phase, end_phase, get_bus, phase, set_bus, tick_phase, use_bus
from .events import (
    EventKind,
    Heartbeat,
    PhaseBegin,
    PhaseEnd,
    PhaseProgress,
    PhaseStatus,
    ProgressEvent,
    event_from_dict,
    event_to_dict,
)
from .sinks.base import ProgressSink

__all__ = [
    "EventKind",
    "Heartbeat",
    "PhaseBegin",
    "PhaseEnd",
    "PhaseProgress",
    "PhaseStatus",
    "ProgressBus",
    "ProgressEvent",
    "ProgressSink",
    "StderrBridge",
    "begin_phase",
    "end_phase",
    "event_from_dict",
    "event_to_dict",
    "get_bus",
    "phase",
    "set_bus",
    "tick_phase",
    "use_bus",
]
