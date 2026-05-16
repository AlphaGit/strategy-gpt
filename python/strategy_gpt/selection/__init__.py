"""Overfitting-aware selection layer.

Runs after parameter-optimization search completes and before ``best.json``
is published. Three independent computations operate on the resulting
``trials.parquet`` + ``manifest.json``:

- :mod:`.cscv` — Combinatorially Symmetric Cross-Validation → Probability
  of Backtest Overfitting (PBO).
- :mod:`.dsr` — Deflated Sharpe Ratio (Bailey & López de Prado 2014).
- :mod:`.sensitivity` — k-NN neighborhood parameter-sensitivity scoring.

:mod:`.selector` orchestrates them into a final :class:`SelectionDecision`.
"""

from .cscv import PboKnobs, PboResult, compute_pbo
from .dsr import DsrInput, DsrKnobs, DsrResult, compute_dsr, compute_dsr_top_k
from .selector import (
    SELECTION_METHODOLOGY,
    CandidateScores,
    SelectionCandidate,
    SelectionDecision,
    SelectionKnobs,
    SelectionStatus,
    run_selection,
)
from .sensitivity import SensitivityKnobs, SensitivityResult, TrialPoint, compute_sensitivity

__all__ = [
    "SELECTION_METHODOLOGY",
    "CandidateScores",
    "DsrInput",
    "DsrKnobs",
    "DsrResult",
    "PboKnobs",
    "PboResult",
    "SelectionCandidate",
    "SelectionDecision",
    "SelectionKnobs",
    "SelectionStatus",
    "SensitivityKnobs",
    "SensitivityResult",
    "TrialPoint",
    "compute_dsr",
    "compute_dsr_top_k",
    "compute_pbo",
    "compute_sensitivity",
    "run_selection",
]
