"""Round-trip tests for the pydantic ↔ Rust JSON boundary.

The Rust side emits / consumes JSON with specific shapes (snake_case for
most enums, default PascalCase for `engine_rt::Resolution`, etc.). These
tests assert the pydantic models accept and re-emit those shapes
identically, catching drift between the two sides without requiring the
native module to be built.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from strategy_gpt.types import (
    AdjustmentPolicy,
    Bar,
    BarRequest,
    DatasetResponse,
    DecisionKind,
    DecisionRecord,
    DivergenceReason,
    DivergenceRecord,
    DivergenceSeverity,
    EvaluationOutcome,
    Resolution,
    RunnerVersion,
    RunRecord,
    ValidationReport,
)


def test_bar_round_trip() -> None:
    raw = {
        "symbol": "VXX",
        "ts": "2024-01-01T00:00:00Z",
        "resolution": "Day",
        "open": 50.0,
        "high": 51.0,
        "low": 49.5,
        "close": 50.5,
        "volume": 1000.0,
    }
    bar = Bar.model_validate(raw)
    assert bar.resolution is Resolution.DAY
    parsed = json.loads(bar.model_dump_json())
    assert parsed["resolution"] == "Day"


def test_bar_request_default_secondary_providers() -> None:
    req = BarRequest(
        provider="yf",
        symbol="VXX",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 12, 31, tzinfo=UTC),
        resolution=Resolution.DAY,
        adjustment=AdjustmentPolicy.BACK_ADJUSTED,
    )
    parsed = json.loads(req.model_dump_json())
    assert parsed["adjustment"] == "back_adjusted"
    assert parsed["secondary_providers"] == []


def test_dataset_response_with_warning() -> None:
    raw = {
        "bars": [],
        "manifest": ["abc", "def"],
        "manifest_hash": "deadbeef",
        "warnings": [
            {
                "symbol": "VXX",
                "ts": "2024-06-01T00:00:00Z",
                "providers": ["yf", "polygon"],
                "values": {"yf": {"close": 12.0}, "polygon": {"close": 12.05}},
                "reason": "close_mismatch",
                "severity": "warn",
            }
        ],
    }
    resp = DatasetResponse.model_validate(raw)
    assert resp.manifest_hash == "deadbeef"
    assert resp.warnings[0].reason is DivergenceReason.CLOSE_MISMATCH
    assert resp.warnings[0].severity is DivergenceSeverity.WARN


def test_run_record_optional_fields() -> None:
    raw = {
        "id": "run-1",
        "strategy_artifact": "art-1",
        "dataset_manifest_hash": "hash-1",
        "parameters": {},
        "modes": [],
        "seed": 0,
        "runner_version": {"major": 0, "minor": 1, "patch": 0},
        "slice": {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-12-31T00:00:00Z",
        },
        "engine_config": {
            "fill_model": "NextBarOpen",
            "initial_capital": 100000.0,
            "commission_per_fill": 0.0,
            "slippage_bps": 0.0,
            "sanity": {
                "max_intent_size": 1.0e9,
                "max_position_size": 1.0e9,
            },
        },
        "parallelism": 1,
        "created_at": "2024-01-01T00:00:00Z",
    }
    rec = RunRecord.model_validate(raw)
    assert rec.hypothesis_id is None
    assert rec.sidecar_root is None
    assert rec.runner_version == RunnerVersion(major=0, minor=1, patch=0)
    assert rec.engine_config.fill_model.value == "NextBarOpen"
    assert rec.slice.start.year == 2024
    assert rec.parallelism == 1


def test_decision_record_kind_enum() -> None:
    rec = DecisionRecord(
        id="d-1",
        hypothesis_id="h-1",
        kind=DecisionKind.ACCEPTED,
        rationale="passed",
        evidence={"score": 1.5},
        decided_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    parsed = json.loads(rec.model_dump_json())
    assert parsed["kind"] == "accepted"


def test_evaluation_outcome_shape() -> None:
    o = EvaluationOutcome(accepted=True, score=1.2, violations=[], soft_misses=["sharpe"])
    parsed = json.loads(o.model_dump_json())
    assert parsed == {
        "accepted": True,
        "score": 1.2,
        "violations": [],
        "soft_misses": ["sharpe"],
    }


def test_validation_report_failure() -> None:
    r = ValidationReport(ok=False, errors=["unknown metric `made_up`"])
    assert r.ok is False
    assert r.errors[0].startswith("unknown metric")


@pytest.mark.parametrize(
    ("rust_value", "py_enum"),
    [
        ("Minute", Resolution.MINUTE),
        ("FiveMinute", Resolution.FIVE_MINUTE),
        ("FifteenMinute", Resolution.FIFTEEN_MINUTE),
        ("Hour", Resolution.HOUR),
        ("Day", Resolution.DAY),
        ("Week", Resolution.WEEK),
    ],
)
def test_resolution_variants_match_rust(rust_value: str, py_enum: Resolution) -> None:
    parsed = Resolution(rust_value)
    assert parsed is py_enum


def test_divergence_record_volume_mismatch() -> None:
    rec = DivergenceRecord(
        symbol="VXX",
        ts=datetime(2024, 6, 1, tzinfo=UTC),
        providers=["yf", "polygon"],
        values={},
        reason=DivergenceReason.VOLUME_MISMATCH,
        severity=DivergenceSeverity.INFO,
    )
    parsed = json.loads(rec.model_dump_json())
    assert parsed["reason"] == "volume_mismatch"
    assert parsed["severity"] == "info"
