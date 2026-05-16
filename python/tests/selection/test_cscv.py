"""Unit tests for CSCV / PBO."""

from __future__ import annotations

import math
import random

import pytest

from strategy_gpt.selection.cscv import (
    PboKnobs,
    compute_pbo,
    enumerate_splits,
    sample_splits,
)


def test_enumerate_splits_counts_match_binomial() -> None:
    splits = list(enumerate_splits(8))
    assert len(splits) == math.comb(8, 4)
    splits_6 = list(enumerate_splits(6))
    assert len(splits_6) == math.comb(6, 3)
    # Every split partitions the index range.
    for a, b in splits:
        assert set(a).isdisjoint(b)
        assert set(a) | set(b) == set(range(8))
        assert len(a) == 4
        assert len(b) == 4


def test_enumerate_splits_rejects_odd() -> None:
    with pytest.raises(ValueError, match="even"):
        list(enumerate_splits(7))


def test_sample_splits_deterministic_and_unique() -> None:
    a = list(sample_splits(20, 50, seed=1))
    b = list(sample_splits(20, 50, seed=1))
    assert a == b
    keys = {tuple(a_) for a_, _ in a}
    assert len(keys) == 50


def test_pbo_random_noise_is_around_half() -> None:
    """Random objective → average PBO across many independent matrices ≈ 0.5.

    Single-realization PBO has a wide ±0.15 sampling band even at N=50,
    S=8; averaging across many seeds removes that noise so the assertion
    pins the population mean instead of a single draw.
    """
    n, s = 50, 8
    n_runs = 20
    pbos: list[float] = []
    for seed in range(n_runs):
        rng = random.Random(seed)
        matrix = [[rng.gauss(0.0, 1.0) for _ in range(s)] for _ in range(n)]
        out = compute_pbo(matrix, PboKnobs())
        pbos.append(out.pbo)
        assert out.n_folds == s
        assert out.n_trials == n
        assert out.enumerated is True
        assert out.n_splits == math.comb(s, s // 2)
    mean_pbo = sum(pbos) / len(pbos)
    assert 0.40 <= mean_pbo <= 0.60


def test_pbo_signal_rich_is_low() -> None:
    """One trial consistently dominates on every fold → PBO near 0."""
    n, s = 20, 8
    matrix: list[list[float]] = []
    for i in range(n):
        # Trial 0 beats every other trial on every fold.
        baseline = -float(i)
        matrix.append([baseline + 0.0 for _ in range(s)])
    knobs = PboKnobs()
    out = compute_pbo(matrix, knobs)
    assert out.pbo == 0.0


def test_pbo_overfit_signal_is_high() -> None:
    """A trial that wins IS by chance but loses OOS → high PBO.

    Construct a matrix where each trial's metric on every fold is
    independent noise, but inject a small structured asymmetry so the
    IS-best is biased toward landing in the OOS bottom half. The simpler
    canonical degenerate case: all-zeros matrix → ties → IS-best ranks at
    the median → PBO ≈ 0.5; we already cover ~0.5 in
    :func:`test_pbo_random_noise_is_around_half`. This test pins the
    perfect-overfit extreme: every trial does well in exactly one fold
    and poorly in all others, so the IS-best on any one fold is the
    OOS-worst on every other fold.
    """
    n = 8
    s = 8  # one strong fold per trial; surplus trials reuse fold 0.
    matrix = [[0.0] * s for _ in range(n)]
    for i in range(n):
        matrix[i][i % s] = 10.0  # huge IS bump.
        for j in range(s):
            if j != i % s:
                matrix[i][j] = -1.0  # poor OOS on every other fold.
    out = compute_pbo(matrix, PboKnobs())
    assert out.pbo >= 0.8


def test_pbo_records_seed_when_sampling() -> None:
    """S > 16 forces Monte Carlo; the seed is recorded for replay."""
    rng = random.Random(0)
    n, s = 20, 18
    matrix = [[rng.gauss(0.0, 1.0) for _ in range(s)] for _ in range(n)]
    knobs = PboKnobs(max_splits=200)
    out = compute_pbo(matrix, knobs, seed=7)
    assert out.enumerated is False
    assert out.seed == 7
    assert out.n_splits == 200
    # Re-run with the same seed → identical PBO.
    out2 = compute_pbo(matrix, knobs, seed=7)
    assert out.pbo == out2.pbo


def test_pbo_drops_odd_trailing_fold() -> None:
    """S odd → drop the last fold so the split halves stay equal."""
    n, s = 10, 9
    rng = random.Random(3)
    matrix = [[rng.gauss(0.0, 1.0) for _ in range(s)] for _ in range(n)]
    out = compute_pbo(matrix, PboKnobs())
    assert out.n_folds == s - 1


def test_pbo_degenerate_returns_zero() -> None:
    out = compute_pbo([], PboKnobs())
    assert out.pbo == 0.0
    out = compute_pbo([[1.0]], PboKnobs())
    assert out.pbo == 0.0
