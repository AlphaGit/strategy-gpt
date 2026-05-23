"""Typed progress events emitted during the author emit/build/smoke loop.

These events are the substrate behind the in-flight operator feedback in
the CLI and the test harness's event-sequence assertions. They are also
how a future programmatic caller (e.g. the hypothesis loop's `generate`
stage) can observe the author run without reaching into stdout.

The event sink is a simple callable. The default sink in
:class:`strategy_gpt.author.AuthorDeps` is a no-op so library callers
opt in by passing one explicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RepairAttemptStarted:
    """A new attempt of the emit / build / smoke loop is starting."""

    attempt: int
    budget: int
    event_type: Literal["repair_attempt_started"] = "repair_attempt_started"


@dataclass(frozen=True)
class RepairAttemptCompleted:
    """An attempt finished; ``outcome`` is the validate-step verdict."""

    attempt: int
    outcome: str
    event_type: Literal["repair_attempt_completed"] = "repair_attempt_completed"


@dataclass(frozen=True)
class FileWritten:
    """The LLM-emitted file at ``path`` was written to disk."""

    path: str
    event_type: Literal["file_written"] = "file_written"


@dataclass(frozen=True)
class LintStarted:
    """Lint pass is about to run on the emitted source + manifest."""

    event_type: Literal["lint_started"] = "lint_started"


@dataclass(frozen=True)
class LintCompleted:
    """Lint pass finished; ``ok`` reports whether it accepted the emission."""

    ok: bool
    event_type: Literal["lint_completed"] = "lint_completed"


@dataclass(frozen=True)
class CargoBuildStarted:
    """A package-scoped ``cargo build`` is about to run."""

    args: tuple[str, ...]
    event_type: Literal["cargo_build_started"] = "cargo_build_started"


@dataclass(frozen=True)
class CargoBuildProgress:
    """Heartbeat emitted at regular intervals while ``cargo build`` is in flight.

    The native BuildPipeline shells out to cargo via a blocking call,
    so the orchestrator spawns a watcher thread that emits this event
    every few seconds. The CLI renderer turns these into a "still
    building" tick so the operator can see progress is happening even
    when the build takes 30s+.
    """

    elapsed_seconds: float
    event_type: Literal["cargo_build_progress"] = "cargo_build_progress"


@dataclass(frozen=True)
class CargoBuildCompleted:
    """The cargo build finished with ``returncode`` in ``duration`` seconds."""

    returncode: int
    duration_seconds: float
    event_type: Literal["cargo_build_completed"] = "cargo_build_completed"


@dataclass(frozen=True)
class SmokeFetchStarted:
    """Smoke fixture data fetch is about to start."""

    symbol: str
    start: str
    end: str
    event_type: Literal["smoke_fetch_started"] = "smoke_fetch_started"


@dataclass(frozen=True)
class SmokeFetchCompleted:
    """Smoke fixture fetch finished."""

    symbol: str
    event_type: Literal["smoke_fetch_completed"] = "smoke_fetch_completed"


@dataclass(frozen=True)
class SmokeRunStarted:
    """Smoke backtest run is about to start."""

    event_type: Literal["smoke_run_started"] = "smoke_run_started"


@dataclass(frozen=True)
class SmokeRunCompleted:
    """Smoke backtest finished; reports trade count and sanity-trip count."""

    ok: bool
    trade_count: int
    sanity_trips: int
    event_type: Literal["smoke_run_completed"] = "smoke_run_completed"


AuthorEvent = (
    RepairAttemptStarted
    | RepairAttemptCompleted
    | FileWritten
    | LintStarted
    | LintCompleted
    | CargoBuildStarted
    | CargoBuildProgress
    | CargoBuildCompleted
    | SmokeFetchStarted
    | SmokeFetchCompleted
    | SmokeRunStarted
    | SmokeRunCompleted
)


AuthorEventSink = Callable[[AuthorEvent], None]
"""Callable that consumes :class:`AuthorEvent` instances.

The default sink is a no-op so library callers can ignore the stream.
The CLI installs a sink that renders events as progress lines.
"""


def noop_sink(_event: AuthorEvent) -> None:
    """Sink that drops every event. Used as the default in ``AuthorDeps``."""


def collecting_sink() -> tuple[list[AuthorEvent], AuthorEventSink]:
    """Return ``(events, sink)`` where ``sink`` appends to ``events``.

    Convenience for tests that need to assert event order without
    writing a small class each time.
    """
    events: list[AuthorEvent] = []
    return events, events.append


__all__: Sequence[str] = (
    "AuthorEvent",
    "AuthorEventSink",
    "CargoBuildCompleted",
    "CargoBuildProgress",
    "CargoBuildStarted",
    "FileWritten",
    "LintCompleted",
    "LintStarted",
    "RepairAttemptCompleted",
    "RepairAttemptStarted",
    "SmokeFetchCompleted",
    "SmokeFetchStarted",
    "SmokeRunCompleted",
    "SmokeRunStarted",
    "collecting_sink",
    "noop_sink",
)
