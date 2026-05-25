"""--progress flag resolver: maps mode -> ProgressSink list."""

from __future__ import annotations

import sys
from enum import StrEnum
from typing import TextIO

from .base import NullSink, ProgressSink
from .jsonl import JsonlSink
from .plain import PlainSink
from .rich_live import RichLiveSink


class ProgressMode(StrEnum):
    AUTO = "auto"
    PLAIN = "plain"
    JSON = "json"
    OFF = "off"


def resolve_sink(
    mode: ProgressMode | str,
    *,
    stderr: TextIO | None = None,
    isatty_override: bool | None = None,
) -> list[ProgressSink]:
    """Return the sink list for the chosen mode.

    `auto` selects RichLiveSink when stderr is a TTY, else JsonlSink.
    `off` returns an empty list (no sink installed); the caller may
    treat that as "skip the bus" or install a NullSink — both behave
    identically under the spec.
    """
    m = ProgressMode(mode) if isinstance(mode, str) else mode
    stream: TextIO = stderr if stderr is not None else sys.stderr
    if m is ProgressMode.OFF:
        return []
    if m is ProgressMode.JSON:
        return [JsonlSink(stream=stream)]
    if m is ProgressMode.PLAIN:
        return [PlainSink(stream=stream)]
    # AUTO
    is_tty = isatty_override if isatty_override is not None else _isatty(stream)
    if is_tty:
        from rich.console import Console  # noqa: PLC0415 — defer rich import

        console = Console(file=stream, force_terminal=True, stderr=(stream is sys.stderr))
        return [RichLiveSink(console=console)]
    return [JsonlSink(stream=stream)]


def _isatty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


__all__ = ["NullSink", "ProgressMode", "resolve_sink"]
