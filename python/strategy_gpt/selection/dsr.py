"""Deflated Sharpe Ratio (Bailey & López de Prado 2014).

For a single trial's primary Sharpe ``SR_i``, the DSR is:

.. math::

   \\mathrm{DSR}_i = \\Phi\\!\\left(
       \\frac{SR_i - \\mathbb{E}[\\max SR \\mid N, \\gamma_3, \\gamma_4]}
            {\\sqrt{\\mathrm{Var}(SR_i)}}
   \\right)

with

- :math:`\\mathbb{E}[\\max SR \\mid N] \\approx (1-\\gamma)\\,\\Phi^{-1}\\!(1 - 1/N)
  + \\gamma\\,\\Phi^{-1}\\!(1 - 1/(N\\,e))` and :math:`\\gamma`
  Euler-Mascheroni — the expected maximum Sharpe under the null.
- :math:`\\mathrm{Var}(SR_i) = (1 - \\gamma_3 \\cdot SR_i + (\\gamma_4 - 1)/4 \\cdot SR_i^2)
  / (T - 1)` accounting for non-normality of returns; :math:`T` = trade count.

When the strategy's return moments are unobservable (only aggregate
metrics are recorded per trial), the layer falls back to the Gaussian
assumption :math:`\\gamma_3 = 0, \\gamma_4 = 3`, which collapses the
variance term to :math:`(1 + SR_i^2/2) / (T-1)` — the conservative
normal-returns approximation Bailey & López de Prado note as the
default. The fallback is recorded in the manifest so downstream consumers
know which assumptions held.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .normal import phi, phi_inv

_EULER_MASCHERONI = 0.5772156649015329

EffectiveN = Literal["distinct_params", "trial_count"]


class DsrKnobs(BaseModel):
    """Tunable knobs for the DSR computation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    top_k: int = Field(default=50, ge=2)
    effective_n: EffectiveN = "distinct_params"


@dataclass(frozen=True)
class DsrInput:
    """One candidate's DSR inputs.

    ``trade_count`` is ``n_trades`` from the engine's aggregate metrics.
    ``skew`` / ``kurt`` of the returns distribution are unobservable from
    the recorded aggregate metrics — pass them through when a downstream
    integration has them; otherwise leave the defaults (``0`` / ``3``).
    """

    sharpe: float
    trade_count: int
    skew: float = 0.0
    kurt: float = 3.0


@dataclass(frozen=True)
class DsrResult:
    """DSR output for a single candidate."""

    sharpe: float
    expected_max_sharpe: float
    sharpe_variance: float
    z: float
    dsr: float


def expected_max_sharpe(effective_n: int) -> float:
    """``E[max SR | N]`` under the null (Bailey & López de Prado 2014, eq. 7).

    For ``N <= 1`` returns 0.0 — only one trial, no inflation to deflate.
    """
    if effective_n <= 1:
        return 0.0
    n = float(effective_n)
    term1 = phi_inv(1.0 - 1.0 / n)
    term2 = phi_inv(1.0 - 1.0 / (n * math.e))
    return (1.0 - _EULER_MASCHERONI) * term1 + _EULER_MASCHERONI * term2


def sharpe_variance(sharpe: float, trade_count: int, skew: float, kurt: float) -> float:
    """Bailey/López de Prado 2014 eq. 6 — variance of the SR estimator."""
    if trade_count <= 1:
        return float("inf")
    return (1.0 - skew * sharpe + (kurt - 1.0) / 4.0 * sharpe**2) / (trade_count - 1)


def compute_dsr(candidate: DsrInput, *, effective_n: int) -> DsrResult:
    """Compute the DSR for one candidate against ``effective_n`` trials."""
    e_max = expected_max_sharpe(effective_n)
    var = sharpe_variance(candidate.sharpe, candidate.trade_count, candidate.skew, candidate.kurt)
    if var <= 0.0 or not math.isfinite(var):
        return DsrResult(
            sharpe=candidate.sharpe,
            expected_max_sharpe=e_max,
            sharpe_variance=var,
            z=float("-inf"),
            dsr=0.0,
        )
    z = (candidate.sharpe - e_max) / math.sqrt(var)
    return DsrResult(
        sharpe=candidate.sharpe,
        expected_max_sharpe=e_max,
        sharpe_variance=var,
        z=z,
        dsr=phi(z),
    )


def compute_dsr_top_k(
    candidates: Sequence[DsrInput],
    *,
    effective_n: int,
) -> list[DsrResult]:
    """Compute DSR for a list of candidates against the same effective N."""
    return [compute_dsr(c, effective_n=effective_n) for c in candidates]


__all__ = [
    "DsrInput",
    "DsrKnobs",
    "DsrResult",
    "EffectiveN",
    "compute_dsr",
    "compute_dsr_top_k",
    "expected_max_sharpe",
    "sharpe_variance",
]
