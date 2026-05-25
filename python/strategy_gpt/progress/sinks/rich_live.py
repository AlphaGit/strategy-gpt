"""Rich live renderer: phase-tree + nested bars on stderr, <=10 Hz refresh."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from ..events import Heartbeat, PhaseBegin, PhaseEnd, PhaseProgress, ProgressEvent

REFRESH_PER_SECOND = 10


@dataclass(slots=True)
class _PhaseRow:
    path: str
    started_at: float
    total: int | None = None
    current: int = 0
    msg: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    closed: bool = False
    status: str = "running"
    wall_secs: float = 0.0


class RichLiveSink:
    """Wrap a rich.Live driving a phase tree.

    Constructed lazily on first event so a no-op run incurs no terminal
    setup. `close()` exits the Live context and emits a final rendered
    frame so cancellations show the last state.
    """

    def __init__(
        self,
        *,
        console: Console | None = None,
        refresh_per_second: int = REFRESH_PER_SECOND,
    ) -> None:
        self._console = console if console is not None else Console(stderr=True)
        self._refresh = refresh_per_second
        self._live: Live | None = None
        self._phases: dict[str, _PhaseRow] = {}
        self._order: list[str] = []
        self._started = False

    def _ensure_live(self) -> Live:
        if self._live is None:
            self._live = Live(
                self._render(),
                console=self._console,
                refresh_per_second=self._refresh,
                transient=False,
            )
            self._live.start()
            self._started = True
        return self._live

    def handle(self, event: ProgressEvent) -> None:  # noqa: PLR0912 — one branch per event/field
        live = self._ensure_live()
        if isinstance(event, PhaseBegin):
            self._phases[event.path] = _PhaseRow(
                path=event.path,
                started_at=event.started_at or event.emitted_at,
                total=event.total,
                msg=event.msg,
            )
            if event.path not in self._order:
                self._order.append(event.path)
        elif isinstance(event, PhaseProgress):
            row = self._phases.get(event.path)
            if row is None:
                row = _PhaseRow(path=event.path, started_at=event.emitted_at)
                self._phases[event.path] = row
                self._order.append(event.path)
            row.current = max(row.current, event.current)
            if event.total is not None:
                row.total = event.total
            if event.msg is not None:
                row.msg = event.msg
            if event.metrics:
                row.metrics.update({k: float(v) for k, v in event.metrics.items()})
        elif isinstance(event, PhaseEnd):
            row = self._phases.get(event.path)
            if row is not None:
                row.closed = True
                row.status = event.status.value
                row.wall_secs = event.wall_secs
                if event.msg is not None:
                    row.msg = event.msg
                if event.metrics:
                    row.metrics.update({k: float(v) for k, v in event.metrics.items()})
        elif isinstance(event, Heartbeat):
            row = self._phases.get(event.path)
            if row is not None:
                row.msg = f"(heartbeat {event.since_last_event_secs:.0f}s idle)"
        live.update(self._render())

    def _render(self) -> Tree:
        root = Tree("progress")
        # Render in insertion order so sibling phases share parent prefix.
        for path in self._order:
            row = self._phases.get(path)
            if row is None:
                continue
            root.add(self._render_row(row))
        return root

    def _render_row(self, row: _PhaseRow) -> Any:  # noqa: ANN401 — rich primitives lack a public base type
        elapsed = max(0.0, (row.wall_secs if row.closed else time.monotonic() - row.started_at))
        status_glyph = self._glyph(row.status, row.closed)
        head = Table.grid(padding=(0, 1))
        head.add_column()
        head.add_column()
        if row.total is not None and row.total > 0 and not row.closed:
            pct = min(100, (row.current * 100) // max(1, row.total))
            bar = f"[{pct:3d}%] {row.current}/{row.total}"
            head.add_row(
                Text(status_glyph),
                Text(f"{row.path} {bar} {elapsed:.1f}s{self._fmt_meta(row)}"),
            )
        elif not row.closed:
            head.add_row(
                Spinner("dots", text=Text(f"{row.path} {elapsed:.1f}s{self._fmt_meta(row)}")),
                Text(""),
            )
        else:
            head.add_row(
                Text(status_glyph),
                Text(f"{row.path} {row.status} {elapsed:.2f}s{self._fmt_meta(row)}"),
            )
        return head

    @staticmethod
    def _glyph(status: str, closed: bool) -> str:
        if not closed:
            return "▶"
        return {"ok": "✓", "fail": "✗", "skip": "·", "cancelled": "⊘"}.get(status, "?")

    @staticmethod
    def _fmt_meta(row: _PhaseRow) -> str:
        parts: list[str] = []
        if row.metrics:
            mparts = [f"{k}={v:.4g}" for k, v in row.metrics.items()]
            parts.append("{" + ", ".join(mparts) + "}")
        if row.msg:
            parts.append(row.msg)
        return (" " + " ".join(parts)) if parts else ""

    def close(self) -> None:
        if self._live is not None and self._started:
            try:
                self._live.update(self._render())
                self._live.stop()
            except Exception:
                return
            finally:
                self._live = None


__all__ = ["REFRESH_PER_SECOND", "RichLiveSink"]
