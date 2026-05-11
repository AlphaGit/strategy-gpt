"""Pydantic mirrors of the Rust serde types crossed via the PyO3 boundary.

These models exist so the orchestrator code has typed Python objects to
manipulate; serialization to/from JSON matches the Rust ``Serialize`` /
``Deserialize`` derives in `engine-rt`, `data-gateway`, `ledger`, and
`objectives`. Any drift between this module and the Rust side will surface
as a `pydantic.ValidationError` at deserialization time.

Conventions
-----------
- `datetime` fields use timezone-aware UTC; the Rust side uses `chrono::Utc`.
- Enum values are the literal strings emitted by serde's snake_case rename
  (where applicable) — see each enum's docstring.
- Models are `frozen=True` to mirror the immutability of records that travel
  through the append-only ledger.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# engine-rt: Bar, Resolution
# ---------------------------------------------------------------------------


class Resolution(StrEnum):
    """Bar resolution. Serde derives default (PascalCase) on the Rust side."""

    MINUTE = "Minute"
    FIVE_MINUTE = "FiveMinute"
    FIFTEEN_MINUTE = "FifteenMinute"
    HOUR = "Hour"
    DAY = "Day"
    WEEK = "Week"


class Bar(BaseModel):
    """OHLCV bar. Mirrors `engine_rt::Bar`."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    resolution: Resolution
    open: float
    high: float
    low: float
    close: float
    volume: float


# ---------------------------------------------------------------------------
# data-gateway: BarRequest, AdjustmentPolicy, DatasetResponse, DivergenceRecord
# ---------------------------------------------------------------------------


class AdjustmentPolicy(StrEnum):
    """Whether bar prices have been split/dividend-adjusted."""

    RAW = "raw"
    BACK_ADJUSTED = "back_adjusted"


class BarRequest(BaseModel):
    """Mirrors `data_gateway::BarRequest`."""

    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    start: datetime
    end: datetime
    resolution: Resolution
    adjustment: AdjustmentPolicy
    secondary_providers: list[str] = Field(default_factory=list)


class DivergenceReason(StrEnum):
    CLOSE_MISMATCH = "close_mismatch"
    VOLUME_MISMATCH = "volume_mismatch"
    BAR_MISSING = "bar_missing"


class DivergenceSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class DivergenceRecord(BaseModel):
    """Cross-provider disagreement record from the consolidator."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    providers: list[str]
    values: dict[str, Any]
    reason: DivergenceReason
    severity: DivergenceSeverity


class DatasetResponse(BaseModel):
    """Mirrors `data_gateway::DatasetResponse`."""

    model_config = ConfigDict(frozen=True)

    bars: list[Bar]
    manifest: list[str]
    manifest_hash: str
    warnings: list[DivergenceRecord] = Field(default_factory=list)


CacheMode = Literal["prefer_cache", "validate", "force_refresh", "offline"]


# ---------------------------------------------------------------------------
# ledger: record types
# ---------------------------------------------------------------------------


class RunnerVersion(BaseModel):
    """Engine ABI version. Mirrors `engine_rt::RunnerVersion`."""

    model_config = ConfigDict(frozen=True)

    major: int
    minor: int
    patch: int


class TimeRange(BaseModel):
    """Half-open `[start, end)` UTC slice. Mirrors `engine::spec::TimeRange`."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime


class FillModel(StrEnum):
    """Mirrors `engine::fill_model::FillModel`."""

    NEXT_BAR_OPEN = "NextBarOpen"
    CURRENT_BAR_CLOSE = "CurrentBarClose"


class SanityBounds(BaseModel):
    """Mirrors `engine::sanity::SanityBounds`."""

    model_config = ConfigDict(frozen=True)

    max_intent_size: float
    max_position_size: float


class EngineConfig(BaseModel):
    """Mirrors `engine::spec::EngineConfig`. Required for byte-identical replay."""

    model_config = ConfigDict(frozen=True)

    fill_model: FillModel
    initial_capital: float
    commission_per_fill: float
    slippage_bps: float
    sanity: SanityBounds


class RunRecord(BaseModel):
    """Mirrors `ledger::records::RunRecord`."""

    model_config = ConfigDict(frozen=True)

    id: str
    strategy_artifact: str
    dataset_manifest_hash: str
    hypothesis_id: str | None = None
    parameters: Any
    modes: Any
    seed: int
    runner_version: RunnerVersion
    slice: TimeRange
    engine_config: EngineConfig
    parallelism: int
    verdict: Any | None = None
    metrics: Any | None = None
    sidecar_root: str | None = None
    created_at: datetime


class HypothesisRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    target_metric: str
    falsification: Any
    proposed_change: Any
    kb_cites: Any
    created_at: datetime


class DecisionKind(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    hypothesis_id: str
    kind: DecisionKind
    rationale: str
    evidence: Any
    decided_at: datetime


class DivergenceWarning(BaseModel):
    """Ledger-side divergence record. Translated from
    `data_gateway::DivergenceRecord` by the orchestrator."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    providers: list[str]
    values: Any
    reason: str
    severity: DivergenceSeverity
    logged_at: datetime


# ---------------------------------------------------------------------------
# objectives module — evaluator outcome and validation report
# ---------------------------------------------------------------------------


class EvaluationOutcome(BaseModel):
    """Result of scoring a `BacktestMetrics` against an `ObjectiveSpec`."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    score: float
    violations: list[str]
    soft_misses: list[str]


class ValidationReport(BaseModel):
    """Result of `objectives::validate` over an `ObjectiveSpec`."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    errors: list[str]


__all__ = [
    "AdjustmentPolicy",
    "Bar",
    "BarRequest",
    "CacheMode",
    "DatasetResponse",
    "DecisionKind",
    "DecisionRecord",
    "DivergenceReason",
    "DivergenceRecord",
    "DivergenceSeverity",
    "DivergenceWarning",
    "EngineConfig",
    "EvaluationOutcome",
    "FillModel",
    "HypothesisRecord",
    "Resolution",
    "RunRecord",
    "RunnerVersion",
    "SanityBounds",
    "TimeRange",
    "ValidationReport",
]
