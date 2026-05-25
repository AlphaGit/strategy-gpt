"""Ambient ProgressBus context.

Long-running call paths (optimization, hypothesis loop, tester, smoke,
gateway fetch) cross many modules; threading a `bus` argument through
every signature is invasive and clouds business code. We install a
process-wide `ContextVar` holding the active bus; helpers
(`begin_phase`, `tick_phase`, `end_phase`) consult it and no-op when no
bus is installed.

The CLI sets the contextvar at the top of each command and clears it on
teardown. Library entry points do not need to know progress exists.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING

from .events import PhaseStatus

if TYPE_CHECKING:
    from .bus import ProgressBus

_BUS: ContextVar[ProgressBus | None] = ContextVar("strategy_gpt_progress_bus", default=None)


def set_bus(bus: ProgressBus | None) -> None:
    """Install or clear the ambient bus for the current context."""
    _BUS.set(bus)


def get_bus() -> ProgressBus | None:
    return _BUS.get()


@contextlib.contextmanager
def use_bus(bus: ProgressBus | None) -> Iterator[ProgressBus | None]:
    """Scoped install. Restores the previous value on exit."""
    token = _BUS.set(bus)
    try:
        yield bus
    finally:
        _BUS.reset(token)


def begin_phase(
    path: str,
    *,
    total: int | None = None,
    unit: str | None = None,
    msg: str | None = None,
) -> None:
    bus = _BUS.get()
    if bus is None:
        return
    bus.begin(path, total=total, unit=unit, msg=msg)


def tick_phase(
    path: str,
    current: int,
    *,
    total: int | None = None,
    msg: str | None = None,
    metrics: Mapping[str, float] | None = None,
) -> None:
    bus = _BUS.get()
    if bus is None:
        return
    bus.tick(path, current, total=total, msg=msg, metrics=metrics)


def end_phase(
    path: str,
    *,
    status: PhaseStatus = PhaseStatus.OK,
    msg: str | None = None,
    metrics: Mapping[str, float] | None = None,
) -> None:
    bus = _BUS.get()
    if bus is None:
        return
    bus.end(path, status=status, msg=msg, metrics=metrics)


@contextlib.contextmanager
def phase(
    path: str,
    *,
    total: int | None = None,
    unit: str | None = None,
    msg: str | None = None,
) -> Iterator[None]:
    """Context-managed phase. Begins on entry; ends with the appropriate
    status based on whether the block raised."""
    begin_phase(path, total=total, unit=unit, msg=msg)
    try:
        yield
    except BaseException:
        end_phase(path, status=PhaseStatus.FAIL)
        raise
    else:
        end_phase(path, status=PhaseStatus.OK)


__all__ = [
    "begin_phase",
    "end_phase",
    "get_bus",
    "phase",
    "set_bus",
    "tick_phase",
    "use_bus",
]
