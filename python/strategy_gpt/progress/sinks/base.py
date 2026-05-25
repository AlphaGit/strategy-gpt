"""Sink protocol: every sink consumes ProgressEvents."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..events import ProgressEvent


@runtime_checkable
class ProgressSink(Protocol):
    """Receive ProgressEvents and render / write them. Stateful sinks may
    buffer; `close()` flushes and releases resources."""

    def handle(self, event: ProgressEvent) -> None: ...

    def close(self) -> None: ...


class NullSink:
    """Drops every event. Selected by `--progress=off`."""

    def handle(self, event: ProgressEvent) -> None:
        return

    def close(self) -> None:
        return


__all__ = ["NullSink", "ProgressSink"]
