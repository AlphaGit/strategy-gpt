"""Unit tests for :func:`strategy_gpt.folds.derive_folds`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from strategy_gpt.experiment_spec import FoldsBlock
from strategy_gpt.folds import derive_folds
from strategy_gpt.types import TimeRange


def _base(years: int = 8) -> TimeRange:
    start = datetime(2018, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=365 * years)
    return TimeRange(start=start, end=end)


@pytest.mark.parametrize("count", [2, 4, 8])
@pytest.mark.parametrize("scheme", ["rolling", "anchored"])
def test_derive_folds_count_matches(count: int, scheme: str) -> None:
    base = _base()
    folds = FoldsBlock(count=count, scheme=scheme)  # type: ignore[arg-type]
    out = derive_folds(base, folds)
    assert len(out) == count


def test_rolling_pairs_tile_with_uniform_step() -> None:
    base = _base(years=8)
    folds = FoldsBlock(count=4, scheme="rolling", gap=0)
    out = derive_folds(base, folds)
    total = (base.end - base.start).total_seconds()
    seg = total / (2 * 4)
    assert pytest.approx(seg) == (out[0].train.end - out[0].train.start).total_seconds()
    # Each fold shifts forward by exactly 2 segments.
    for i in range(len(out) - 1):
        delta = (out[i + 1].train.start - out[i].train.start).total_seconds()
        assert pytest.approx(delta) == 2 * seg


def test_anchored_keeps_train_start_constant() -> None:
    base = _base(years=8)
    folds = FoldsBlock(count=4, scheme="anchored", gap=0)
    out = derive_folds(base, folds)
    train_starts = {f.train.start for f in out}
    assert train_starts == {base.start}
    # Train end grows monotonically.
    for i in range(len(out) - 1):
        assert out[i].train.end < out[i + 1].train.end


@pytest.mark.parametrize("gap", [0, 1, 5])
def test_gap_shrinks_train_window(gap: int) -> None:
    base = _base(years=8)
    if gap >= 8:
        pytest.skip("gap must be < count")
    folds = FoldsBlock(count=8, scheme="rolling", gap=gap)
    out = derive_folds(base, folds)
    total = (base.end - base.start).total_seconds()
    seg = total / (2 * 8)
    expected_train_len = seg * (1 - gap)
    actual = (out[0].train.end - out[0].train.start).total_seconds()
    # gap=0 → train spans 1 segment; gap=1 → 0 segments (degenerate but valid input)
    assert pytest.approx(actual) == expected_train_len


def test_warmup_only_attached_to_first_fold() -> None:
    base = _base()
    folds = FoldsBlock(count=4, scheme="rolling", gap=0, warmup_bars=20)
    out = derive_folds(base, folds)
    assert out[0].warmup_bars == 20
    assert all(f.warmup_bars is None for f in out[1:])


def test_oos_immediately_follows_train_when_no_gap() -> None:
    base = _base()
    folds = FoldsBlock(count=4, scheme="rolling", gap=0)
    out = derive_folds(base, folds)
    for f in out:
        assert f.train.end == f.oos.start


def test_rejects_count_below_two() -> None:
    base = _base()
    # FoldsBlock pydantic validation also enforces this; build by bypassing
    # via model_construct so we exercise the runtime check too.
    folds = FoldsBlock.model_construct(count=1, scheme="rolling", gap=0, warmup_bars=None)
    with pytest.raises(ValueError, match="count must be >= 2"):
        derive_folds(base, folds)


def test_rejects_empty_slice() -> None:
    folds = FoldsBlock(count=2, scheme="rolling")
    bad = TimeRange(
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2019, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="empty or reversed"):
        derive_folds(bad, folds)
