"""Python wrapper around the PyO3 `Ledger` class.

Adds typed inputs/outputs on top of the native module's JSON boundary.
Every method that records data accepts a pydantic record; reads return
pydantic records (or `None` where the underlying SQL query returns no rows).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal, TypeVar, cast

from pydantic import BaseModel

from ._native_shim import require_native
from .types import (
    DecisionRecord,
    DivergenceWarning,
    HypothesisRecord,
    RunRecord,
)

SidecarKind = Literal["trades", "signals", "equity", "exec_log"]

_VALID_SIDECARS: Final[frozenset[str]] = frozenset(("trades", "signals", "equity", "exec_log"))

T = TypeVar("T", bound=BaseModel)


class Ledger:
    """High-level wrapper over `strategy_gpt._native.ledger.Ledger`."""

    def __init__(self, root: Path | str) -> None:
        native = require_native()
        self._led = native.ledger.Ledger(str(root))

    @property
    def root(self) -> str:
        result: str = self._led.root()
        return result

    def record_run(self, record: RunRecord) -> None:
        self._led.record_run(record.model_dump_json())

    def record_hypothesis(self, record: HypothesisRecord) -> None:
        self._led.record_hypothesis(record.model_dump_json())

    def record_decision(self, record: DecisionRecord) -> None:
        self._led.record_decision(record.model_dump_json())

    def record_divergence(self, record: DivergenceWarning) -> None:
        self._led.record_divergence(record.model_dump_json())

    def get_run(self, run_id: str) -> RunRecord | None:
        raw: str | None = self._led.get_run(run_id)
        if raw is None:
            return None
        return RunRecord.model_validate_json(raw)

    def recent_decisions(self, limit: int) -> str:
        """Return the raw JSON array of recent decisions joined with hypotheses.

        Returned as JSON to match the Rust `Vec<RecentDecision>` shape; the
        join-result type is intentionally not duplicated in Python because
        the orchestrator consumes it as opaque context for the LLM prompt.
        """
        result: str = self._led.recent_decisions(limit)
        return result

    def store_sidecar(self, run_id: str, kind: SidecarKind, records_json: str) -> None:
        if kind not in _VALID_SIDECARS:
            msg = f"unknown sidecar kind `{kind}`"
            raise ValueError(msg)
        self._led.store_sidecar(run_id, kind, records_json)

    def load_sidecar(self, run_id: str, kind: SidecarKind) -> str:
        if kind not in _VALID_SIDECARS:
            msg = f"unknown sidecar kind `{kind}`"
            raise ValueError(msg)
        result: str = self._led.load_sidecar(run_id, kind)
        return result


# Cast helper kept for symmetry with future typed sidecar wrappers.
_ = cast


__all__ = ["Ledger", "SidecarKind"]
