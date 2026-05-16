"""Fold derivation from an experiment-spec ``folds`` block.

Pure-Python helper that translates a declarative
:class:`strategy_gpt.experiment_spec.FoldsBlock` plus a base
:class:`strategy_gpt.types.TimeRange` slice into a list of
``(train, oos)`` time-range pairs.

Two schemes are supported:

- ``rolling`` — equal-width sliding window; both ``train`` and ``oos``
  segments slide together over the slice. Useful when regime drift means
  recent history matters more than ancient history.
- ``anchored`` — train start is pinned to the slice start; train end
  grows fold by fold; OOS segments slide. Useful when more data is
  always better for the in-sample fit.

The base slice is split into ``2 * count`` equal-width segments. Each
fold (i ∈ 0..count) gets segments [2i, 2i+1] as (train, oos):
``rolling`` uses pair (2i, 2i+1); ``anchored`` extends train to start
at fold-0's train start.

``gap`` removes that many segment-units from the *end* of each train
window so the OOS window does not start adjacent to the train window.
``warmup_bars`` is interpreted as a fixed-bar prelude carved off the
front of the slice for fold 0's train window only; it is forwarded as
``warmup_bars`` on the first fold's :class:`FoldRange` so the engine
can honour it.

Calendar arithmetic uses :class:`datetime.timedelta`. Bars are not
inspected — the slice is sliced purely by wall time. If
``warmup_bars`` is set, callers are responsible for translating that
back to a bar count when wiring the result into the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .types import TimeRange

if TYPE_CHECKING:
    from .experiment_spec import FoldsBlock


MIN_FOLD_COUNT = 2


@dataclass(frozen=True)
class FoldRange:
    """One fold: a ``(train, oos)`` pair plus any per-fold warmup."""

    train: TimeRange
    oos: TimeRange
    warmup_bars: int | None = None


def derive_folds(base: TimeRange, folds: FoldsBlock) -> list[FoldRange]:
    """Translate ``folds`` (declarative) into a list of ``(train, oos)`` ranges.

    Args:
        base: The full experiment slice. Must be ``end > start``.
        folds: The declarative fold-block from the experiment-spec.

    Returns:
        Exactly ``folds.count`` :class:`FoldRange` entries in chronological
        order.
    """
    if base.end <= base.start:
        msg = f"derive_folds: base slice is empty or reversed ({base.start} → {base.end})."
        raise ValueError(msg)
    if folds.count < MIN_FOLD_COUNT:
        msg = f"derive_folds: count must be >= {MIN_FOLD_COUNT}, got {folds.count}."
        raise ValueError(msg)

    total_seconds = (base.end - base.start).total_seconds()
    segment_count = 2 * folds.count
    if folds.gap >= folds.count:
        msg = f"derive_folds: gap ({folds.gap}) must be smaller than count ({folds.count})."
        raise ValueError(msg)
    seg_seconds = total_seconds / segment_count

    def at(units: float) -> datetime:
        return base.start + timedelta(seconds=seg_seconds * units)

    out: list[FoldRange] = []
    warmup = folds.warmup_bars
    for i in range(folds.count):
        train_start_unit = 0.0 if folds.scheme == "anchored" else float(2 * i)
        train_end_unit = float(2 * i + 1 - folds.gap)
        oos_start_unit = float(2 * i + 1)
        oos_end_unit = float(2 * i + 2)

        train = TimeRange(start=at(train_start_unit), end=at(train_end_unit))
        oos = TimeRange(start=at(oos_start_unit), end=at(oos_end_unit))
        out.append(
            FoldRange(
                train=train,
                oos=oos,
                warmup_bars=warmup if i == 0 else None,
            )
        )
    return out


__all__ = ["FoldRange", "derive_folds"]
