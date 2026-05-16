"""Unit tests for the Deflated Sharpe Ratio."""

from __future__ import annotations

import math

import pytest

from strategy_gpt.selection.dsr import (
    DsrInput,
    compute_dsr,
    expected_max_sharpe,
    sharpe_variance,
)
from strategy_gpt.selection.normal import phi, phi_inv


def test_phi_round_trip() -> None:
    for p in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99):
        assert abs(phi(phi_inv(p)) - p) < 1e-9


def test_phi_known_values() -> None:
    assert abs(phi(0.0) - 0.5) < 1e-12
    assert abs(phi(1.96) - 0.9750021048) < 1e-6
    assert abs(phi(-1.96) - 0.0249978952) < 1e-6


def test_expected_max_sharpe_grows_with_n() -> None:
    e1 = expected_max_sharpe(10)
    e2 = expected_max_sharpe(100)
    e3 = expected_max_sharpe(1000)
    assert 0.0 < e1 < e2 < e3
    # ``E[max SR]`` for N=1 is zero (the single trial has nothing to beat).
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(0) == 0.0


def test_sharpe_variance_matches_closed_form() -> None:
    # Normal returns: skew=0, kurt=3 → variance simplifies to (1 + SR^2/2)/(T-1).
    sr, t = 1.5, 251
    var = sharpe_variance(sr, t, 0.0, 3.0)
    expected = (1.0 + sr**2 / 2.0) / (t - 1)
    assert abs(var - expected) < 1e-12


def test_dsr_hand_computable() -> None:
    """Hand-computable example pins the formula to 1e-6 tolerance.

    Inputs: SR = 2.0, T = 250 trades, N = 100 effective trials, normal
    returns (skew=0, kurt=3). Plug into the formula and compare the
    DSR with the value the module produces.
    """
    sr, t, n = 2.0, 250, 100
    e_max = expected_max_sharpe(n)
    var = (1.0 + sr**2 / 2.0) / (t - 1)
    z = (sr - e_max) / math.sqrt(var)
    expected_dsr = phi(z)
    out = compute_dsr(DsrInput(sharpe=sr, trade_count=t), effective_n=n)
    assert abs(out.expected_max_sharpe - e_max) < 1e-9
    assert abs(out.sharpe_variance - var) < 1e-12
    assert abs(out.z - z) < 1e-9
    assert abs(out.dsr - expected_dsr) < 1e-6


def test_dsr_handles_tiny_trade_count() -> None:
    out = compute_dsr(DsrInput(sharpe=2.0, trade_count=1), effective_n=10)
    assert out.dsr == 0.0
    assert math.isinf(out.sharpe_variance) or out.sharpe_variance == float("inf")


def test_dsr_ranks_lower_sharpe_lower() -> None:
    high = compute_dsr(DsrInput(sharpe=3.0, trade_count=500), effective_n=100)
    low = compute_dsr(DsrInput(sharpe=1.0, trade_count=500), effective_n=100)
    assert high.dsr > low.dsr


def test_phi_inv_rejects_out_of_domain() -> None:
    with pytest.raises(ValueError, match="0, 1"):
        phi_inv(0.0)
    with pytest.raises(ValueError, match="0, 1"):
        phi_inv(1.0)
