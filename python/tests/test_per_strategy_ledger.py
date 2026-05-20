"""Unit tests for the per-strategy hypothesis ledger."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_gpt.per_strategy_ledger import (
    STAGE1,
    STAGE2,
    STAGE3,
    AddedParam,
    DecisionRecordV2,
    DecisionStage,
    Falsification,
    FalsificationPrimary,
    FalsificationScope,
    GuardConstraint,
    HypothesisRecordV2,
    ParamIntent,
    PerStrategyLedger,
    RepairAttempt,
    StageResponses,
    canonical_files_set_hash,
)


def _ledger(tmp_path: Path, strategy: str = "vxx_volatility_range") -> PerStrategyLedger:
    return PerStrategyLedger(root=tmp_path, strategy=strategy)


def _hypothesis(id_: str = "h1") -> HypothesisRecordV2:
    return HypothesisRecordV2(
        id=id_,
        strategy="vxx_volatility_range",
        candidate_name="add_drawdown_guard",
        files_manifest={"src/lib.rs": "deadbeef", "Cargo.toml": "cafebabe"},
        deleted_files=[],
        baseline_files_hash="baseline-hash",
        param_intent=ParamIntent(
            added=[
                AddedParam(name="dd_cap", kind="f64", min=0.05, max=0.25, default=0.15),
            ],
            kept=["vol_lo", "vol_hi"],
            removed=[],
        ),
        falsification=Falsification(
            primary=FalsificationPrimary(
                metric="sharpe",
                direction="gt",
                delta_vs_baseline=0.20,
                scope=FalsificationScope(kind="aggregate"),
            ),
            guard_constraints=[
                GuardConstraint(metric="max_drawdown", direction="lte", delta_vs_baseline=0.05),
            ],
        ),
        expected_lift_confidence=0.6,
        expected_side_effects=["~30% trade-count decrease"],
        rationale="Add drawdown guard to cut tail-risk in vol blowouts.",
        stage_responses=StageResponses(stage1_hash="s1", stage2_hash="s2", stage3_hash="s3"),
        kb_cites=[],
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
    )


def _decision(hid: str, did: str, kind: str = "accepted") -> DecisionRecordV2:
    return DecisionRecordV2(
        id=did,
        hypothesis_id=hid,
        strategy="vxx_volatility_range",
        outcome=DecisionStage(kind=kind, stage=None if kind == "accepted" else "build"),
        rationale="passed all gates" if kind == "accepted" else "build error",
        evidence={},
        repair_attempts=[],
        decided_at=datetime(2026, 5, 20, 12, tzinfo=UTC),
    )


# ---------------- source-set CAS ----------------


def test_write_source_set_is_content_addressed(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    files = {"Cargo.toml": "x", "src/lib.rs": "y"}
    h1 = led.write_source_set(files)
    h2 = led.write_source_set(files)
    assert h1 == h2
    assert (led.sources_dir / h1).is_dir()
    assert h1 == canonical_files_set_hash(files)


def test_source_set_distinguishes_content(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    h_a = led.write_source_set({"src/lib.rs": "a"})
    h_b = led.write_source_set({"src/lib.rs": "b"})
    assert h_a != h_b


def test_source_set_round_trip(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    files = {"Cargo.toml": '[package]\nname="x"\n', "src/lib.rs": "// rs\n"}
    h = led.write_source_set(files)
    back = led.read_source_set(h)
    assert back == files


def test_source_set_dedupes_across_calls(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    files = {"a.txt": "1"}
    h = led.write_source_set(files)
    # Modify on disk; re-running write_source_set with same content must
    # NOT touch the dedup'd directory (because the call sees the path
    # already exists and is a no-op).
    (led.sources_dir / h / "a.txt").write_text("tampered")
    h2 = led.write_source_set(files)
    assert h == h2
    assert (led.sources_dir / h / "a.txt").read_text() == "tampered"


def test_canonical_hash_ignores_dict_order() -> None:
    h1 = canonical_files_set_hash({"a": "1", "b": "2"})
    h2 = canonical_files_set_hash({"b": "2", "a": "1"})
    assert h1 == h2


# ---------------- response blobs ----------------


def test_response_blobs_co_located_per_decision(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    decision_id = "d-123"
    led.write_response_blob(decision_id, STAGE1, "# idea\n")
    led.write_response_blob(decision_id, STAGE2, "# commitments\n")
    led.write_response_blob(decision_id, STAGE3, "# files\n")
    led.write_response_blob(decision_id, "repair_0", "# repair\n")
    base = led.response_dir(decision_id)
    assert (base / f"{STAGE1}.md").exists()
    assert (base / f"{STAGE2}.md").exists()
    assert (base / f"{STAGE3}.md").exists()
    assert (base / "repair_0.md").exists()
    assert led.read_response_blob(decision_id, STAGE1) == "# idea\n"


# ---------------- baseline cache ----------------


def test_baseline_returns_none_when_absent(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    assert led.read_baseline() is None


def test_baseline_round_trip(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    payload = {"dataset_manifest_hash": "ds-1", "score": 1.23}
    led.write_baseline(payload)
    assert led.read_baseline() == payload


def test_baseline_best_computes_on_miss(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    calls = []

    def compute() -> dict[str, float | str]:
        calls.append(1)
        return {"score": 2.5}

    result = led.baseline_best(dataset_manifest_hash="ds-x", compute_on_miss=compute)
    assert result["score"] == 2.5
    assert result["dataset_manifest_hash"] == "ds-x"
    # Second call hits cache.
    led.baseline_best(dataset_manifest_hash="ds-x", compute_on_miss=compute)
    assert len(calls) == 1


def test_baseline_best_recomputes_on_dataset_change(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    calls = []

    def make(score: float) -> Callable[[], dict[str, float]]:
        def _c() -> dict[str, float]:
            calls.append(score)
            return {"score": score}

        return _c

    led.baseline_best(dataset_manifest_hash="ds-1", compute_on_miss=make(1.0))
    led.baseline_best(dataset_manifest_hash="ds-2", compute_on_miss=make(2.0))
    assert calls == [1.0, 2.0]


def test_baseline_best_raises_on_miss_without_compute(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    with pytest.raises(KeyError):
        led.baseline_best(dataset_manifest_hash="ds-x")


# ---------------- parquet records ----------------


def test_record_hypothesis_round_trip(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    led.record_hypothesis(_hypothesis("h1"))
    led.record_hypothesis(_hypothesis("h2"))
    seen = list(led.hypotheses_iter())
    assert {h.id for h in seen} == {"h1", "h2"}


def test_record_decision_round_trip(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    led.record_decision(_decision("h1", "d1"))
    led.record_decision(_decision("h2", "d2", kind="rejected"))
    seen = list(led.decisions_iter())
    assert {d.id for d in seen} == {"d1", "d2"}
    rejected = next(d for d in seen if d.id == "d2")
    assert rejected.outcome.stage == "build"


def test_recent_decisions_orders_by_decided_at_desc(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    d_old = DecisionRecordV2(
        id="old",
        hypothesis_id="h",
        strategy="vxx_volatility_range",
        outcome=DecisionStage(kind="accepted"),
        rationale="",
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    d_new = DecisionRecordV2(
        id="new",
        hypothesis_id="h",
        strategy="vxx_volatility_range",
        outcome=DecisionStage(kind="accepted"),
        rationale="",
        decided_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    led.record_decision(d_old)
    led.record_decision(d_new)
    recent = led.recent_decisions(limit=10)
    assert [d.id for d in recent] == ["new", "old"]


def test_repair_attempts_persist_on_evidence_chain(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    dr = DecisionRecordV2(
        id="d1",
        hypothesis_id="h1",
        strategy="vxx_volatility_range",
        outcome=DecisionStage(kind="rejected", stage="build"),
        rationale="exhausted repair budget",
        evidence={},
        repair_attempts=[
            RepairAttempt(
                stage="stage3",
                attempt_index=0,
                files_hash="aaa",
                reject_kind="reject_build",
                feedback="cannot find macro `json`",
            ),
            RepairAttempt(
                stage="stage3",
                attempt_index=1,
                files_hash="bbb",
                reject_kind="reject_build",
                feedback="type mismatch",
            ),
        ],
        decided_at=datetime(2026, 5, 20, 12, tzinfo=UTC),
    )
    led.record_decision(dr)
    back = next(led.decisions_iter())
    assert len(back.repair_attempts) == 2
    assert back.repair_attempts[1].feedback == "type mismatch"


def test_strategy_mismatch_rejected(tmp_path: Path) -> None:
    led = _ledger(tmp_path, strategy="alpha")
    with pytest.raises(ValueError, match="does not match ledger"):
        led.record_hypothesis(_hypothesis())  # strategy=vxx_volatility_range


def test_two_strategies_isolated(tmp_path: Path) -> None:
    led_a = _ledger(tmp_path, strategy="alpha")
    led_b = _ledger(tmp_path, strategy="beta")
    led_a.record_hypothesis(
        HypothesisRecordV2(**{**_hypothesis().model_dump(), "id": "ha", "strategy": "alpha"})
    )
    led_b.record_hypothesis(
        HypothesisRecordV2(**{**_hypothesis().model_dump(), "id": "hb", "strategy": "beta"})
    )
    assert [h.id for h in led_a.hypotheses_iter()] == ["ha"]
    assert [h.id for h in led_b.hypotheses_iter()] == ["hb"]
    assert (tmp_path / "strategies" / "alpha" / "hypothesis_records.parquet").exists()
    assert (tmp_path / "strategies" / "beta" / "hypothesis_records.parquet").exists()


def test_rationale_truncated_at_500(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    long = "x" * 700
    h = HypothesisRecordV2(**{**_hypothesis().model_dump(), "rationale": long, "id": "long"})
    led.record_hypothesis(h)
    back = next(led.hypotheses_iter())
    assert len(back.rationale) == 500
