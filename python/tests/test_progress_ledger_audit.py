"""Audit: ledger writers MUST NOT reference progress event payloads.

Static scan of ledger modules. Progress events are a UX channel — they
have no place in the reproducibility surface (ledger rows, parquet
sidecars). If a future change accidentally couples them, this test
fails before the ledger goes out of sync with the byte-identity
contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_LEDGER_MODULES = [
    "strategy_gpt/ledger.py",
    "strategy_gpt/optimization_ledger.py",
    "strategy_gpt/per_strategy_ledger.py",
]

_FORBIDDEN = [
    "ProgressEvent",
    "PhaseBegin",
    "PhaseProgress",
    "PhaseEnd",
    "Heartbeat",
    "ProgressBus",
    "ProgressSink",
]


@pytest.mark.parametrize("module", _LEDGER_MODULES)
def test_ledger_module_does_not_reference_progress(module: str) -> None:
    root = Path(__file__).resolve().parents[1]
    src = (root / module).read_text()
    for token in _FORBIDDEN:
        assert not re.search(rf"\b{re.escape(token)}\b", src), (
            f"{module} references {token!r}; progress events must not enter the ledger"
        )
