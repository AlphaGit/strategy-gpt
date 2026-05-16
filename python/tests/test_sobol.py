"""Unit tests for the Sobol quasi-random searcher."""

from __future__ import annotations

import math
import warnings

import pytest

from strategy_gpt.optimizer import (
    ChoiceParam,
    ContinuousParam,
    IntParam,
    SobolSearcher,
    _next_power_of_two,
)


def test_next_power_of_two() -> None:
    assert _next_power_of_two(1) == 1
    assert _next_power_of_two(2) == 2
    assert _next_power_of_two(3) == 4
    assert _next_power_of_two(7) == 8
    assert _next_power_of_two(256) == 256
    assert _next_power_of_two(300) == 512


def test_sobol_basic_continuous_coverage() -> None:
    """Sobol sequence spreads samples across the unit cube."""
    space = {"x": ContinuousParam(low=0.0, high=1.0), "y": ContinuousParam(low=-1.0, high=1.0)}
    out = list(SobolSearcher(space=space, n_points=128, scramble=False).candidates())
    assert len(out) == 128
    xs = [p["x"] for p in out]
    ys = [p["y"] for p in out]
    # Range coverage.
    assert min(xs) < 0.1
    assert max(xs) > 0.9
    assert min(ys) < -0.8
    assert max(ys) > 0.8
    # Mean near the center.
    assert abs(sum(xs) / len(xs) - 0.5) < 0.05
    assert abs(sum(ys) / len(ys) - 0.0) < 0.1


def test_sobol_deterministic_when_scrambled() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0)}
    a = list(SobolSearcher(space=space, n_points=64, scramble=True, owen_seed=7).candidates())
    b = list(SobolSearcher(space=space, n_points=64, scramble=True, owen_seed=7).candidates())
    assert a == b
    c = list(SobolSearcher(space=space, n_points=64, scramble=True, owen_seed=8).candidates())
    assert a != c


def test_sobol_deterministic_when_unscrambled() -> None:
    space = {"x": ContinuousParam(low=0.0, high=10.0)}
    a = list(SobolSearcher(space=space, n_points=16, scramble=False).candidates())
    b = list(SobolSearcher(space=space, n_points=16, scramble=False).candidates())
    assert a == b


def test_sobol_int_param_yields_integers() -> None:
    space = {"k": IntParam(low=2, high=10)}
    out = list(SobolSearcher(space=space, n_points=64, scramble=True, owen_seed=0).candidates())
    for p in out:
        assert isinstance(p["k"], int)
        assert 2 <= p["k"] <= 10


def test_sobol_choice_param() -> None:
    space = {"mode": ChoiceParam(choices=["a", "b", "c"])}
    out = list(SobolSearcher(space=space, n_points=32, scramble=True, owen_seed=0).candidates())
    seen = {p["mode"] for p in out}
    # All choices should appear in 32 samples.
    assert seen == {"a", "b", "c"}


def test_sobol_non_power_of_two_warns_and_rounds_up() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0)}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = list(
            SobolSearcher(space=space, n_points=100, scramble=True, owen_seed=0).candidates()
        )
    assert len(out) == 128
    assert any("power of two" in str(w.message) for w in caught)


def test_sobol_log_continuous() -> None:
    """Log-scaled continuous param maps the unit interval to log space."""
    space = {"lr": ContinuousParam(low=1e-4, high=1.0, log=True)}
    out = list(SobolSearcher(space=space, n_points=64, scramble=False).candidates())
    lrs = [p["lr"] for p in out]
    # Geometric mean near the geometric center.
    log_mean = sum(math.log(v) for v in lrs) / len(lrs)
    assert abs(log_mean - (math.log(1e-4) + math.log(1.0)) / 2) < 0.5


def test_sobol_empty_space_yields_nothing() -> None:
    out = list(SobolSearcher(space={}, n_points=8).candidates())
    assert out == []


def test_sobol_count_matches_iterated() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0)}
    s = SobolSearcher(space=space, n_points=100)
    assert s.count() == 128
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = list(s.candidates())
    assert len(out) == s.count()


def test_sobol_int_distribution_uniform() -> None:
    """Sobol over an int range uniformly covers buckets."""
    space = {"k": IntParam(low=0, high=7)}  # 8 buckets
    out = list(SobolSearcher(space=space, n_points=128, scramble=True, owen_seed=42).candidates())
    counts = dict.fromkeys(range(8), 0)
    for p in out:
        counts[p["k"]] += 1
    # Each bucket should see roughly 16 samples; allow ±6.
    for c in counts.values():
        assert 10 <= c <= 22


@pytest.mark.parametrize("n", [2, 4, 16, 64, 256])
def test_sobol_unit_interval_starts_with_origin(n: int) -> None:
    """Unscrambled Sobol's first point is the origin (all zeros)."""
    space = {"x": ContinuousParam(low=0.0, high=1.0), "y": ContinuousParam(low=0.0, high=1.0)}
    out = list(SobolSearcher(space=space, n_points=n, scramble=False).candidates())
    assert out[0]["x"] == 0.0
    assert out[0]["y"] == 0.0
