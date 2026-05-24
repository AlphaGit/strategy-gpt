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
from typing import Annotated, Any, Literal

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
    """Decision-level outcome for a candidate hypothesis.

    ``ACCEPTED`` / ``REJECTED`` carry the usual semantics. ``DEFERRED``
    marks a candidate whose *implementation* (build/lint/format/source
    emission) failed — the underlying idea + commitments are still
    valid, so the loop does not bias future ideation against the
    candidate's logic (see ``hypothesis-loop::mechanical-failures-are-
    deferred-not-rejected``).
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


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


# ---------------------------------------------------------------------------
# engine: BacktestResult and its sub-records
# ---------------------------------------------------------------------------


class Side(StrEnum):
    """Mirrors `engine_rt::Side` (serde default = PascalCase)."""

    LONG = "Long"
    SHORT = "Short"


class Trade(BaseModel):
    """Mirrors `engine::result::Trade`."""

    model_config = ConfigDict(frozen=True)

    entry_ts: datetime
    exit_ts: datetime
    symbol: str
    side: Side
    size: float
    entry_price: float
    exit_price: float
    pnl: float
    fees: float
    reason_in: str | None = None
    reason_out: str | None = None
    signals_at_entry: list[str] = Field(default_factory=list)


class EquityPoint(BaseModel):
    """Mirrors `engine::result::EquityPoint`."""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    equity: float
    drawdown: float
    exposure: float


class BacktestMetrics(BaseModel):
    """Mirrors `engine::result::BacktestMetrics`."""

    model_config = ConfigDict(frozen=True)

    sharpe: float
    sortino: float
    profit_factor: float
    win_ratio: float
    max_drawdown: float
    annualized_return: float
    n_trades: int
    avg_trade_length_bars: float


class ResultMeta(BaseModel):
    """Mirrors `engine::result::ResultMeta`."""

    model_config = ConfigDict(frozen=True)

    strategy_artifact: str
    dataset_manifest: str
    seed: int
    runner_version: RunnerVersion


class SignalEvent(BaseModel):
    """Mirrors `engine_rt::SignalEvent`."""

    model_config = ConfigDict(frozen=True)

    name: str
    ts: datetime
    value: float
    fired: bool
    suppressed_by: str | None = None


class DecisionEvent(BaseModel):
    """Mirrors `engine_rt::DecisionEvent`."""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    event: str
    details: Any


class RegimeTag(BaseModel):
    """Mirrors `engine::result::RegimeTag`."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    label: str


class StressScenario(BaseModel):
    """Mirrors `engine::result::StressScenario`."""

    model_config = ConfigDict(frozen=True)

    name: str
    perturbation: Any
    metrics: BacktestMetrics


class StressResult(BaseModel):
    """Mirrors `engine::result::StressResult`."""

    model_config = ConfigDict(frozen=True)

    scenarios: list[StressScenario]


class SensitivityPoint(BaseModel):
    """Mirrors `engine::result::SensitivityPoint`."""

    model_config = ConfigDict(frozen=True)

    value: float
    metrics: BacktestMetrics


class SensitivityResult(BaseModel):
    """Mirrors `engine::result::SensitivityResult`."""

    model_config = ConfigDict(frozen=True)

    param: str
    points: list[SensitivityPoint]


class BacktestResult(BaseModel):
    """Mirrors `engine::result::BacktestResult`. Sole input to the diagnose
    node and a primary cross-FFI artifact."""

    model_config = ConfigDict(frozen=True)

    meta: ResultMeta
    metrics: BacktestMetrics
    trades: list[Trade]
    signals: list[SignalEvent]
    equity: list[EquityPoint]
    exec_log: list[DecisionEvent]
    regimes: list[RegimeTag] = Field(default_factory=list)
    stress: StressResult | None = None
    sensitivity: SensitivityResult | None = None


# ---------------------------------------------------------------------------
# engine: BatchSpec.failure_mode + packed RunResult entries
# ---------------------------------------------------------------------------


class FailureMode(StrEnum):
    """Batch-level failure policy. Mirrors `engine::spec::FailureMode`.

    ``abort`` (default) — first failure cancels remaining runs and surfaces
    as an outer batch error. ``continue`` — per-run failures land in the
    result list as :class:`RunResultFailed` entries.
    """

    ABORT = "abort"
    CONTINUE = "continue"


class RunResultOk(BaseModel):
    """Successful packed-batch entry. Mirrors `engine::RunResult::Ok`."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    run_index: int
    result: BacktestResult


class RunResultFailed(BaseModel):
    """Failed packed-batch entry. Mirrors `engine::RunResult::Failed`."""

    model_config = ConfigDict(frozen=True)

    status: Literal["failed"] = "failed"
    run_index: int
    error_kind: str
    message: str


RunResult = Annotated[RunResultOk | RunResultFailed, Field(discriminator="status")]
"""Discriminated union of packed-batch run outcomes.

Use as ``TypeAdapter(list[RunResult])`` to deserialize a result list
returned by `Engine.poll(handle).results`.
"""


__all__ = [
    "AdjustmentPolicy",
    "BacktestMetrics",
    "BacktestResult",
    "Bar",
    "BarRequest",
    "CacheMode",
    "DatasetResponse",
    "DecisionEvent",
    "DecisionKind",
    "DecisionRecord",
    "DivergenceReason",
    "DivergenceRecord",
    "DivergenceSeverity",
    "DivergenceWarning",
    "EngineConfig",
    "EquityPoint",
    "EvaluationOutcome",
    "FailureMode",
    "FillModel",
    "HypothesisRecord",
    "RegimeTag",
    "Resolution",
    "ResultMeta",
    "RunRecord",
    "RunResult",
    "RunResultFailed",
    "RunResultOk",
    "RunnerVersion",
    "SanityBounds",
    "SensitivityPoint",
    "SensitivityResult",
    "Side",
    "SignalEvent",
    "StressResult",
    "StressScenario",
    "TimeRange",
    "Trade",
    "ValidationReport",
]
