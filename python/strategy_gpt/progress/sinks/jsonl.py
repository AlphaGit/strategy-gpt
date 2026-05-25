"""JSONL sink: one JSON line per ProgressEvent on stderr."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from ..events import ProgressEvent, event_to_dict


class JsonlSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stderr

    def handle(self, event: ProgressEvent) -> None:
        line = json.dumps(event_to_dict(event), separators=(",", ":"))
        self._stream.write(line + "\n")
        self._stream.flush()

    def close(self) -> None:
        try:
            self._stream.flush()
        except (ValueError, OSError):
            # Stream may already be closed at process shutdown.
            return


__all__ = ["JsonlSink"]
