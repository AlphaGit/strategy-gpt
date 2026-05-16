"""Pydantic loader for the ``experiment-spec`` v1 schema.

An experiment-spec is the single declarative file consumed by
``strategy-gpt run --spec <file>``. It fully determines a backtest
experiment: which compiled strategy artifact, which bars source, which
engine configuration, which run list, the parallelism cap, and the
per-run resource caps.

The loader produces an :class:`ExperimentSpec` plus a translation helper
:meth:`ExperimentSpec.to_batch_spec` that emits the inner ``BatchSpec``
dict the engine PyO3 binding still expects. The change is in how callers
*compose* the BatchSpec; the engine's wire shape is unchanged.

Polymorphism of ``bars``: exactly one of ``dataset`` (cache-resident
manifest hash) or ``request`` (a :class:`BarRequest` that the runner
fetches through the gateway before submitting).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .types import (
    BarRequest,
    FillModel,
    SanityBounds,
    TimeRange,
)
from .types import EngineConfig as LedgerEngineConfig


class EngineConfig(BaseModel):
    """User-facing engine configuration. Does NOT carry ``slippage_bps`` —
    per-fill slippage is expressed as a :class:`Slippage` mode on a run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_model: FillModel = FillModel.NEXT_BAR_OPEN
    initial_capital: float = 100_000.0
    commission_per_fill: float = 0.0
    sanity: SanityBounds = SanityBounds(max_intent_size=1.0e9, max_position_size=1.0e9)

    @model_validator(mode="before")
    @classmethod
    def _reject_slippage_bps(cls, value: object) -> object:
        if isinstance(value, dict) and "slippage_bps" in value:
            msg = (
                "engine.slippage_bps is not an engine-config field in experiment-spec; "
                "express per-fill slippage as a `Slippage { bps_grid }` mode on the "
                "affected run(s)."
            )
            raise ValueError(msg)
        return value


class DatasetRef(BaseModel):
    """Cache-resident bars: reference an already-materialized manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: str = Field(..., description="Manifest hash returned by the gateway.")


class RequestRef(BaseModel):
    """Auto-fetch bars: the runner pulls through the gateway before submit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: BarRequest


BarsRef = Annotated[DatasetRef | RequestRef, Field(discriminator=None)]


class RunConfig(BaseModel):
    """One run inside the experiment.

    ``modes`` is a free-form list passed through to the engine. The
    inner shape mirrors ``engine::spec::Mode`` (``{kind: "plain"}``,
    ``{kind: "monte_carlo", n, block_size}``, ``{kind: "slippage", bps_grid}``,
    etc.). The loader does not validate mode shapes — drift would surface
    at the Rust serde boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    params: dict[str, Any] = Field(default_factory=dict)
    modes: list[dict[str, Any]] = Field(default_factory=lambda: [{"kind": "plain"}])
    seed: int = 0
    slice: TimeRange


class Caps(BaseModel):
    """Per-run resource caps forwarded to the engine coordinator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    time_cap_secs: float | None = None
    mem_cap_bytes: int | None = None


_LEGACY_SENTINELS: frozenset[str] = frozenset(("strategy", "dataset"))


class ExperimentSpec(BaseModel):
    """Top-level experiment definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: Path
    bars: BarsRef
    engine: EngineConfig = EngineConfig()
    runs: list[RunConfig] = Field(..., min_length=1)
    parallelism: int | Literal["auto"] = 1
    caps: Caps = Caps()
    strategy_label: str | None = None

    @field_validator("bars", mode="before")
    @classmethod
    def _validate_bars_xor(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        has_dataset = "dataset" in value
        has_request = "request" in value
        if has_dataset and has_request:
            msg = (
                "experiment-spec: `bars` must declare exactly one of "
                "`dataset` or `request`, not both."
            )
            raise ValueError(msg)
        if not has_dataset and not has_request:
            msg = "experiment-spec: `bars` must declare one of `dataset` or `request`."
            raise ValueError(msg)
        return value

    @field_validator("parallelism", mode="after")
    @classmethod
    def _validate_parallelism_int(cls, value: int | str) -> int | str:
        if isinstance(value, int) and value < 1:
            msg = f"experiment-spec: `parallelism` must be >= 1, got {value}."
            raise ValueError(msg)
        return value

    def resolved_parallelism(self) -> int:
        """Resolve ``parallelism: auto`` against the current host."""
        if isinstance(self.parallelism, int):
            return self.parallelism
        return _resolve_auto_parallelism()

    def to_batch_spec(self, dataset_label: str) -> dict[str, Any]:
        """Translate to the inner ``BatchSpec`` dict the engine accepts.

        ``dataset_label`` is the manifest hash (or other opaque label)
        the ledger will record under ``BatchSpec.dataset``.
        """
        engine_cfg = LedgerEngineConfig(
            fill_model=self.engine.fill_model,
            initial_capital=self.engine.initial_capital,
            commission_per_fill=self.engine.commission_per_fill,
            slippage_bps=0.0,
            sanity=self.engine.sanity,
        )
        strategy = self.strategy_label or self.artifact.stem
        return {
            "strategy": strategy,
            "dataset": dataset_label,
            "runs": [json.loads(r.model_dump_json()) for r in self.runs],
            "engine": json.loads(engine_cfg.model_dump_json()),
            "parallelism": self.resolved_parallelism(),
        }


def _resolve_auto_parallelism() -> int:
    """``max(1, usable_cpu_count - 1)`` honoring OS-level affinity."""
    if sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity"):
        usable = len(os.sched_getaffinity(0))
    else:
        usable = os.cpu_count() or 1
    return max(1, usable - 1)


def load(path: Path | str) -> ExperimentSpec:
    """Parse an experiment-spec from ``.yaml`` / ``.yml`` / ``.json``.

    Resolves ``artifact`` relative to the spec file's directory if the
    given path is relative. Rejects the legacy ``batch.json`` shape with
    an explicit migration error.
    """
    p = Path(path)
    raw = p.read_text()
    payload = yaml.safe_load(raw) if p.suffix.lower() in (".yaml", ".yml") else json.loads(raw)
    if not isinstance(payload, dict):
        msg = f"experiment-spec: top-level must be a mapping, got {type(payload).__name__}."
        raise ValueError(msg)
    _reject_legacy(payload)
    spec = ExperimentSpec.model_validate(payload)
    if not spec.artifact.is_absolute():
        resolved = (p.parent / spec.artifact).resolve()
        spec = spec.model_copy(update={"artifact": resolved})
    return spec


def _reject_legacy(payload: dict[str, Any]) -> None:
    """Detect the pre-existing ``batch.json`` top-level shape and bail.

    The legacy shape has top-level ``strategy`` (string) and ``dataset``
    (string) keys alongside ``runs``. The new shape uses ``artifact``
    (path) and ``bars`` (polymorphic block) instead.
    """
    legacy_keys = _LEGACY_SENTINELS & payload.keys()
    if legacy_keys and "bars" not in payload and "artifact" not in payload:
        msg = (
            "legacy `batch.json` format detected; migrate to experiment-spec.yaml — "
            "see docs/experiment-spec.md. Top-level `strategy` and `dataset` strings "
            "are replaced by `artifact: <path>` and `bars: {dataset: <hash>}` or "
            "`bars: {request: ...}`."
        )
        raise ValueError(msg)


__all__ = [
    "BarsRef",
    "Caps",
    "DatasetRef",
    "EngineConfig",
    "ExperimentSpec",
    "RequestRef",
    "RunConfig",
    "load",
]
