"""Tester translation tests — parameter-only fast path and logic-change path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from strategy_gpt.build_pipeline import (
    BuildArtifact,
    BuildErrorKind,
    BuildFailure,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.engine import JobStatus
from strategy_gpt.hypothesis_loop import HypothesisCandidate
from strategy_gpt.tester import (
    FalsificationParseError,
    LogicChangeTranslationError,
    ParamDiff,
    ParamOnlyTranslationError,
    RejectionReason,
    SmokePolicy,
    TesterRejection,
    VerdictKind,
    apply_param_diffs,
    attempt_logic_change,
    build_full_batch_spec,
    evaluate_verdict,
    parse_falsification,
    parse_logic_change,
    parse_param_only_change,
    record_tester_rejection,
    reject_build_failure,
    run_smoke,
    translate_logic_change,
    translate_param_only,
    walk_forward_slices,
)
from strategy_gpt.types import (
    Bar,
    DecisionKind,
    DecisionRecord,
    HypothesisRecord,
    Resolution,
    RunnerVersion,
)


def _candidate(proposed_change: object) -> HypothesisCandidate:
    return HypothesisCandidate(
        name="lower_vol_lo",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change=proposed_change,
        estimated_lift_confidence=0.5,
    )


def test_parse_single_param_diff() -> None:
    diffs = parse_param_only_change({"param": "vol_lo", "from": 10, "to": 5})
    assert diffs == [ParamDiff(param="vol_lo", from_value=10, to_value=5)]


def test_parse_bulk_diffs_preserves_order() -> None:
    diffs = parse_param_only_change(
        {
            "diffs": [
                {"param": "vol_lo", "from": 10, "to": 5},
                {"param": "vol_hi", "from": 30, "to": 25},
            ]
        }
    )
    assert [d.param for d in diffs] == ["vol_lo", "vol_hi"]
    assert diffs[1].to_value == 25


def test_parse_missing_to_raises() -> None:
    with pytest.raises(ParamOnlyTranslationError, match=r"param.*to"):
        parse_param_only_change({"param": "vol_lo", "from": 10})


def test_parse_non_mapping_raises() -> None:
    with pytest.raises(ParamOnlyTranslationError, match="mapping"):
        parse_param_only_change("vol_lo=5")
    with pytest.raises(ParamOnlyTranslationError, match="mapping"):
        parse_param_only_change([{"param": "vol_lo", "to": 5}])


def test_parse_logic_change_keys_routes_caller_elsewhere() -> None:
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        parse_param_only_change({"source": "fn on_bar(...) {}", "diffs": [{"param": "x", "to": 1}]})
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        parse_param_only_change({"rewrite": True})


def test_parse_unrecognized_shape_raises_with_keys() -> None:
    with pytest.raises(ParamOnlyTranslationError, match=r"param.*to.*diffs"):
        parse_param_only_change({"foo": 1, "bar": 2})


def test_apply_param_diffs_preserves_untouched_keys() -> None:
    base = {"vol_lo": 10, "vol_hi": 30, "lookback": 20}
    merged = apply_param_diffs(
        base,
        [
            ParamDiff(param="vol_lo", from_value=10, to_value=5),
            ParamDiff(param="vol_hi", from_value=30, to_value=25),
        ],
    )
    assert merged == {"vol_lo": 5, "vol_hi": 25, "lookback": 20}
    # Returns a copy, not a mutation.
    assert base["vol_lo"] == 10


def test_apply_param_diffs_last_write_wins_for_duplicate_keys() -> None:
    merged = apply_param_diffs(
        {"x": 1},
        [
            ParamDiff(param="x", from_value=1, to_value=2),
            ParamDiff(param="x", from_value=2, to_value=99),
        ],
    )
    assert merged == {"x": 99}


def test_translate_param_only_full_round_trip() -> None:
    cand = _candidate({"param": "vol_lo", "from": 10, "to": 5})
    result = translate_param_only(
        cand,
        strategy_artifact="artifact-abc",
        base_params={"vol_lo": 10, "vol_hi": 30},
    )
    assert result.strategy_artifact == "artifact-abc"
    assert result.params == {"vol_lo": 5, "vol_hi": 30}
    assert result.diffs == [ParamDiff(param="vol_lo", from_value=10, to_value=5)]


def test_translate_param_only_logic_change_raises() -> None:
    cand = _candidate({"source": "fn on_bar(){}", "param": "x", "to": 1})
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        translate_param_only(cand, strategy_artifact="artifact-abc", base_params={})


def test_param_diff_alias_round_trip_via_validate() -> None:
    diff = ParamDiff.model_validate({"param": "x", "from": 1, "to": 2})
    assert diff.from_value == 1
    assert diff.to_value == 2
    # JSON serialization uses field-name (`from_value`) not alias; the
    # ledger stores the parsed shape so this is the audit form.
    payload = diff.model_dump()
    assert payload == {"param": "x", "from_value": 1, "to_value": 2}


# ---------------------------------------------------------------------------
# Logic-change path
# ---------------------------------------------------------------------------


_OK_SOURCE = """
use engine_rt::{Bar, Context, Result, Sealed, Strategy, StrategyMeta};

#[derive(Default)]
pub struct M;
impl Sealed for M {}
impl Strategy for M {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new("m", "0.1.0", "test", "minimal")
    }
    fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> Result<()> { Ok(()) }
}
"""

_OK_MANIFEST_DICT: dict[str, Any] = {
    "name": "m",
    "version": "0.1.0",
    "dependencies": [{"name": "engine-rt", "req": "*"}],
}


def _logic_candidate(proposed_change: object) -> HypothesisCandidate:
    return HypothesisCandidate(
        name="add_entry_filter",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change=proposed_change,
        estimated_lift_confidence=0.5,
    )


class _StubBuildPipeline:
    """Records every (source, manifest) tuple it sees and returns a canned
    outcome. Tests inject :class:`BuildFailure` as the canned value to
    drive the rejection path without invoking real cargo."""

    def __init__(self, outcome: BuildOutcome | BuildFailure) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, StrategyManifest]] = []

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        self.calls.append((source, manifest))
        if isinstance(self.outcome, BuildFailure):
            raise self.outcome
        return self.outcome

    def lint(self, _source: str, _manifest: StrategyManifest) -> LintReport:
        return LintReport(ok=True)


def _ok_outcome() -> BuildOutcome:
    return BuildOutcome(
        kind=BuildOutcomeKind.COMPILED,
        artifact=BuildArtifact(
            key="deadbeef",
            library_path="./test-fixture-libm.so",
            runner_version=RunnerVersion(major=0, minor=1, patch=0),
            source_size_bytes=len(_OK_SOURCE.encode()),
        ),
    )


def test_parse_logic_change_happy_path() -> None:
    payload = parse_logic_change(
        {"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT, "params": {"vol_lo": 5}}
    )
    assert payload.source == _OK_SOURCE
    assert payload.manifest.name == "m"
    assert payload.params == {"vol_lo": 5}


def test_parse_logic_change_omits_params_default_empty() -> None:
    payload = parse_logic_change({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    assert payload.params == {}


def test_parse_logic_change_rejects_param_only_payload() -> None:
    with pytest.raises(LogicChangeTranslationError, match="missing `source`"):
        parse_logic_change({"param": "vol_lo", "to": 5})


def test_parse_logic_change_rejects_missing_manifest() -> None:
    with pytest.raises(LogicChangeTranslationError, match="manifest"):
        parse_logic_change({"source": _OK_SOURCE})


def test_parse_logic_change_rejects_non_mapping() -> None:
    with pytest.raises(LogicChangeTranslationError, match="mapping"):
        parse_logic_change("fn on_bar(){}")


def test_parse_logic_change_propagates_pydantic_error() -> None:
    bad = {"source": _OK_SOURCE, "manifest": {"name": "m"}}  # missing version
    with pytest.raises(LogicChangeTranslationError):
        parse_logic_change(bad)


def test_translate_logic_change_compiled_artifact_propagates() -> None:
    pipeline = _StubBuildPipeline(_ok_outcome())
    cand = _logic_candidate(
        {"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT, "params": {"vol_lo": 7}}
    )
    result = translate_logic_change(
        cand,
        base_params={"vol_lo": 10, "vol_hi": 30},
        build_pipeline=pipeline,
    )
    assert result.strategy_artifact == "./test-fixture-libm.so"
    assert result.params == {"vol_lo": 7, "vol_hi": 30}
    assert result.build_outcome.kind is BuildOutcomeKind.COMPILED
    assert result.payload.manifest.name == "m"
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0][0] == _OK_SOURCE


def test_translate_logic_change_propagates_build_failure() -> None:
    failure = BuildFailure(kind=BuildErrorKind.CARGO, message="error[E0412]: cannot find type")
    pipeline = _StubBuildPipeline(failure)
    cand = _logic_candidate({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    with pytest.raises(BuildFailure) as excinfo:
        translate_logic_change(cand, base_params={}, build_pipeline=pipeline)
    assert excinfo.value.kind is BuildErrorKind.CARGO
    assert "E0412" in excinfo.value.message


def test_translate_logic_change_rejects_non_logic_payload() -> None:
    pipeline = _StubBuildPipeline(_ok_outcome())
    cand = _logic_candidate({"param": "vol_lo", "to": 5})
    with pytest.raises(LogicChangeTranslationError, match="missing `source`"):
        translate_logic_change(cand, base_params={}, build_pipeline=pipeline)
    # Build pipeline must not be invoked for malformed payloads.
    assert pipeline.calls == []


# ---------------------------------------------------------------------------
# Build-failed rejection path
# ---------------------------------------------------------------------------


class _StubLedger:
    """In-memory _LedgerLike for tests."""

    def __init__(self) -> None:
        self.hypotheses: list[HypothesisRecord] = []
        self.decisions: list[DecisionRecord] = []
        self.decisions_json = "[]"

    def recent_decisions(self, _limit: int) -> str:
        return self.decisions_json

    def record_hypothesis(self, record: HypothesisRecord) -> None:
        self.hypotheses.append(record)

    def record_decision(self, record: DecisionRecord) -> None:
        self.decisions.append(record)


def test_reject_build_failure_writes_hypothesis_plus_rejected_decision() -> None:
    ledger = _StubLedger()
    cand = _logic_candidate({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    failure = BuildFailure(kind=BuildErrorKind.CARGO, message="error[E0412]: cannot find type `T`")
    rejection = reject_build_failure(ledger, cand, failure)
    assert isinstance(rejection, TesterRejection)
    assert rejection.reason is RejectionReason.BUILD_FAILED
    assert rejection.rationale == "build_failed: cargo"
    assert rejection.diagnostics == {
        "build_error_kind": "cargo",
        "message": failure.message,
    }
    assert len(ledger.hypotheses) == 1
    assert ledger.hypotheses[0].id == rejection.hypothesis_id
    assert ledger.hypotheses[0].name == cand.name
    assert len(ledger.decisions) == 1
    decision = ledger.decisions[0]
    assert decision.id == rejection.decision_id
    assert decision.hypothesis_id == rejection.hypothesis_id
    assert decision.kind is DecisionKind.REJECTED
    assert decision.evidence == rejection.diagnostics


def test_reject_build_failure_carries_lint_kind() -> None:
    ledger = _StubLedger()
    cand = _logic_candidate({"source": "pub unsafe fn evil() {}", "manifest": _OK_MANIFEST_DICT})
    failure = BuildFailure(
        kind=BuildErrorKind.SOURCE_LINT,
        message="source: `unsafe` block is not permitted in strategy source",
    )
    rejection = reject_build_failure(ledger, cand, failure)
    assert rejection.diagnostics["build_error_kind"] == "source_lint"
    assert ledger.decisions[0].rationale == "build_failed: source_lint"


def test_reject_build_failure_handles_logic_change_parse_error() -> None:
    ledger = _StubLedger()
    cand = _logic_candidate({"param": "vol_lo", "to": 5})
    err = LogicChangeTranslationError("proposed_change is missing `source`")
    rejection = reject_build_failure(ledger, cand, err)
    assert rejection.diagnostics["build_error_kind"] == "logic_change_parse"
    assert "missing `source`" in rejection.diagnostics["message"]
    assert ledger.decisions[0].rationale == "build_failed: logic_change_parse"


def test_attempt_logic_change_returns_translated_on_success() -> None:
    ledger = _StubLedger()
    pipeline = _StubBuildPipeline(_ok_outcome())
    cand = _logic_candidate({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    result = attempt_logic_change(ledger, pipeline, cand, base_params={"x": 1})
    assert not isinstance(result, TesterRejection)
    assert result.strategy_artifact == "./test-fixture-libm.so"
    # No rejection rows.
    assert ledger.hypotheses == []
    assert ledger.decisions == []


def test_attempt_logic_change_records_rejection_on_build_failure() -> None:
    ledger = _StubLedger()
    failure = BuildFailure(kind=BuildErrorKind.MANIFEST_LINT, message="dep `tokio` not allowed")
    pipeline = _StubBuildPipeline(failure)
    cand = _logic_candidate({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    result = attempt_logic_change(ledger, pipeline, cand, base_params={})
    assert isinstance(result, TesterRejection)
    assert result.reason is RejectionReason.BUILD_FAILED
    assert result.diagnostics["build_error_kind"] == "manifest_lint"
    assert len(ledger.decisions) == 1


def test_attempt_logic_change_records_rejection_on_parse_failure() -> None:
    ledger = _StubLedger()
    pipeline = _StubBuildPipeline(_ok_outcome())
    cand = _logic_candidate({"param": "vol_lo", "to": 5})
    result = attempt_logic_change(ledger, pipeline, cand, base_params={})
    assert isinstance(result, TesterRejection)
    assert result.diagnostics["build_error_kind"] == "logic_change_parse"
    # Pipeline.build must not be invoked on parse failures.
    assert pipeline.calls == []


# ---------------------------------------------------------------------------
# Smoke backtest
# ---------------------------------------------------------------------------


class _StubEngine:
    """Plays back a scripted list of :class:`JobStatus` payloads, one per poll.

    The smoke runner polls until a terminal status arrives; tests
    construct a stub with the desired terminal status in the last slot
    (and ``running`` placeholders before it, if delay simulation matters
    — for these unit tests we go straight to the terminal state to keep
    runtime negligible).
    """

    def __init__(self, statuses: list[JobStatus]) -> None:
        self._statuses = statuses
        self.submits: list[tuple[str, list[Bar], dict[str, Any], str]] = []
        self.polls = 0
        self.dropped: list[str] = []

    def submit_batch(
        self,
        artifact_path: str,
        bars: list[Bar],
        spec: dict[str, Any],
        dataset_manifest: str,
        *,
        run_id: str | None = None,  # parity with engine.py signature
    ) -> str:
        _ = run_id
        self.submits.append((artifact_path, bars, spec, dataset_manifest))
        return "handle-0"

    def poll(self, _handle: str) -> JobStatus:
        idx = min(self.polls, len(self._statuses) - 1)
        self.polls += 1
        return self._statuses[idx]

    def drop_handle(self, handle: str) -> bool:
        self.dropped.append(handle)
        return True


_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_T1 = datetime(2024, 1, 14, tzinfo=UTC)


def _bar(day: int) -> Bar:
    return Bar(
        symbol="VXX",
        ts=_T0 + timedelta(days=day),
        resolution=Resolution.DAY,
        open=10.0,
        high=11.0,
        low=9.5,
        close=10.5,
        volume=1000.0,
    )


def _completed(results: list[dict[str, Any]]) -> JobStatus:
    return JobStatus(status="completed", results=results)


def _failed(message: str) -> JobStatus:
    return JobStatus(status="failed", error=message)


def _ok_metrics_dict() -> dict[str, Any]:
    return {
        "sharpe": 1.7,
        "sortino": 2.1,
        "profit_factor": 1.5,
        "win_ratio": 0.55,
        "max_drawdown": 0.12,
        "annualized_return": 0.20,
        "n_trades": 30,
        "avg_trade_length_bars": 4.0,
    }


def _result_with_trades(
    n_trades: int, exec_log: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    trades = [{"id": i} for i in range(n_trades)]
    return {
        "trades": trades,
        "exec_log": exec_log or [],
        "metrics": _ok_metrics_dict(),
    }


def test_run_smoke_passes_when_trades_emitted() -> None:
    engine = _StubEngine([_completed([_result_with_trades(3)])])
    outcome = run_smoke(
        engine,
        strategy_artifact="./test-fixture-libm.so",
        dataset_ref="vxx-2024",
        bars=[_bar(i) for i in range(5)],
        params={"vol_lo": 5},
        slice_start=_T0,
        slice_end=_T1,
        dataset_manifest="manifest-1",
        policy=SmokePolicy(min_trades=1, poll_interval_secs=0.0),
    )
    assert outcome.ok is True
    assert outcome.rationale == "smoke_passed"
    assert outcome.metrics is not None
    assert outcome.metrics.sharpe == 1.7
    assert engine.dropped == ["handle-0"]
    # Spec should be a single-run plain mode.
    submitted_spec = engine.submits[0][2]
    assert submitted_spec["parallelism"] == 1
    assert submitted_spec["runs"][0]["modes"] == [{"kind": "plain"}]


def test_run_smoke_no_trades_fails_when_min_trades_one() -> None:
    engine = _StubEngine([_completed([_result_with_trades(0)])])
    outcome = run_smoke(
        engine,
        strategy_artifact="./test-fixture-libm.so",
        dataset_ref="vxx-2024",
        bars=[_bar(0)],
        params={},
        slice_start=_T0,
        slice_end=_T1,
        dataset_manifest="m",
        policy=SmokePolicy(min_trades=1, poll_interval_secs=0.0),
    )
    assert outcome.ok is False
    assert "no_trades" in outcome.rationale
    assert outcome.diagnostics["trades_observed"] == 0


def test_run_smoke_engine_failure_surfaced() -> None:
    engine = _StubEngine([_failed("worker panicked at runtime.rs:42")])
    outcome = run_smoke(
        engine,
        strategy_artifact="./test-fixture-libm.so",
        dataset_ref="vxx-2024",
        bars=[_bar(0)],
        params={},
        slice_start=_T0,
        slice_end=_T1,
        dataset_manifest="m",
        policy=SmokePolicy(poll_interval_secs=0.0),
    )
    assert outcome.ok is False
    assert outcome.rationale == "smoke_failed: engine_failed"
    assert "panicked" in outcome.diagnostics["message"]


def test_run_smoke_sanity_violation_tagged() -> None:
    engine = _StubEngine([_failed("RiskCap: intent size 1e15 exceeds sanity bound 1e9")])
    outcome = run_smoke(
        engine,
        strategy_artifact="./test-fixture-libm.so",
        dataset_ref="vxx-2024",
        bars=[_bar(0)],
        params={},
        slice_start=_T0,
        slice_end=_T1,
        dataset_manifest="m",
        policy=SmokePolicy(poll_interval_secs=0.0),
    )
    assert outcome.ok is False
    assert outcome.diagnostics["kind"] == "sanity"


# ---------------------------------------------------------------------------
# Full batch spec
# ---------------------------------------------------------------------------


def test_walk_forward_slices_partitions_range() -> None:
    slices = walk_forward_slices(_T0, _T0 + timedelta(days=12), 3)
    assert len(slices) == 3
    assert slices[0][0] == _T0
    assert slices[-1][1] == _T0 + timedelta(days=12)
    # Contiguous, half-open.
    for prev, current in pairwise(slices):
        assert prev[1] == current[0]


def test_walk_forward_slices_rejects_invalid_folds() -> None:
    with pytest.raises(ValueError, match="folds"):
        walk_forward_slices(_T0, _T1, 0)


def test_walk_forward_slices_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="forward"):
        walk_forward_slices(_T1, _T0, 3)


def test_build_full_batch_spec_one_run_per_fold_with_modes() -> None:
    spec = build_full_batch_spec(
        strategy_artifact="art-1",
        dataset_ref="vxx-2024",
        params={"vol_lo": 5},
        slice_start=_T0,
        slice_end=_T0 + timedelta(days=30),
        folds=3,
        stress_modes=[{"kind": "monte_carlo", "n": 100, "block_size": 5}],
        sensitivity_modes=[{"kind": "sensitivity", "param": "vol_lo", "values": [3.0, 5.0, 7.0]}],
        seed=42,
        parallelism=4,
    )
    assert spec["strategy"] == "art-1"
    assert spec["parallelism"] == 4
    assert len(spec["runs"]) == 3
    # Per-run seeds are stable, distinct, and offset from `seed`.
    assert [r["seed"] for r in spec["runs"]] == [42, 43, 44]
    modes = spec["runs"][0]["modes"]
    assert modes[0] == {"kind": "plain"}
    assert {"kind": "monte_carlo", "n": 100, "block_size": 5} in modes
    assert any(m.get("kind") == "sensitivity" for m in modes)


def test_build_full_batch_spec_defaults_engine_config() -> None:
    spec = build_full_batch_spec(
        strategy_artifact="art-1",
        dataset_ref="vxx-2024",
        params={},
        slice_start=_T0,
        slice_end=_T1,
    )
    assert spec["engine"]["fill_model"] == "NextBarOpen"
    assert spec["engine"]["initial_capital"] == 100_000.0
    assert spec["engine"]["sanity"]["max_intent_size"] == 1e9
    assert len(spec["runs"]) == 1


# ---------------------------------------------------------------------------
# Verdict evaluation
# ---------------------------------------------------------------------------


def test_parse_falsification_full_shape() -> None:
    crit = parse_falsification(
        {"metric": "sharpe", "op": ">=", "threshold": 1.5},
        default_metric="sortino",
    )
    assert crit.metric == "sharpe"
    assert crit.op == ">="
    assert crit.threshold == 1.5


def test_parse_falsification_uses_target_metric_when_absent() -> None:
    crit = parse_falsification({"op": ">=", "threshold": 1.5}, default_metric="sharpe")
    assert crit.metric == "sharpe"


def test_parse_falsification_rejects_bad_op() -> None:
    with pytest.raises(FalsificationParseError, match="op"):
        parse_falsification({"op": "approx", "threshold": 1.5}, default_metric="sharpe")


def test_parse_falsification_rejects_non_numeric_threshold() -> None:
    with pytest.raises(FalsificationParseError, match="threshold"):
        parse_falsification({"op": ">=", "threshold": "high"}, default_metric="sharpe")


def test_evaluate_verdict_passed() -> None:
    cand = HypothesisCandidate(
        name="lift_sharpe",
        target_metric="sharpe",
        falsification={"op": ">=", "threshold": 1.5},
        proposed_change={"param": "x", "to": 1},
        estimated_lift_confidence=0.5,
    )
    verdict = evaluate_verdict(cand, _ok_metrics_dict())
    assert verdict.kind is VerdictKind.PASSED
    assert verdict.criterion.metric == "sharpe"
    assert verdict.observed == 1.7
    assert "satisfies" in verdict.rationale


def test_evaluate_verdict_failed() -> None:
    cand = HypothesisCandidate(
        name="raise_floor",
        target_metric="sharpe",
        falsification={"op": ">=", "threshold": 2.0},
        proposed_change={"param": "x", "to": 1},
        estimated_lift_confidence=0.5,
    )
    verdict = evaluate_verdict(cand, _ok_metrics_dict())
    assert verdict.kind is VerdictKind.FAILED
    assert "fails" in verdict.rationale


def test_evaluate_verdict_metric_override() -> None:
    cand = HypothesisCandidate(
        name="cap_dd",
        target_metric="sharpe",
        falsification={"metric": "max_drawdown", "op": "<=", "threshold": 0.10},
        proposed_change={"param": "x", "to": 1},
        estimated_lift_confidence=0.5,
    )
    verdict = evaluate_verdict(cand, _ok_metrics_dict())
    assert verdict.criterion.metric == "max_drawdown"
    assert verdict.kind is VerdictKind.FAILED  # 0.12 > 0.10


def test_evaluate_verdict_unknown_metric_raises() -> None:
    cand = HypothesisCandidate(
        name="bogus",
        target_metric="ulcer_index",
        falsification={"op": ">=", "threshold": 0.5},
        proposed_change={"param": "x", "to": 1},
        estimated_lift_confidence=0.5,
    )
    with pytest.raises(FalsificationParseError, match="ulcer_index"):
        evaluate_verdict(cand, _ok_metrics_dict())


def test_record_tester_rejection_carries_extra_diagnostics() -> None:
    ledger = _StubLedger()
    cand = _logic_candidate({"source": _OK_SOURCE, "manifest": _OK_MANIFEST_DICT})
    rejection = record_tester_rejection(
        ledger,
        cand,
        reason=RejectionReason.SMOKE_FAILED,
        rationale="smoke_failed: panic on bar 1",
        diagnostics={"panic": "unwrap on None", "bars_processed": 1},
    )
    assert rejection.reason is RejectionReason.SMOKE_FAILED
    decision = ledger.decisions[0]
    assert decision.evidence == {"panic": "unwrap on None", "bars_processed": 1}
