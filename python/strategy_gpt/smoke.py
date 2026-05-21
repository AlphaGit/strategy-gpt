"""End-to-end smoke run for the rewritten hypothesis loop.

Drives the full flow (`diagnose -> kb_query -> kb_filter -> stage1 ->
cheap_critique -> stage2 -> stage3 -> mini_optimize -> mechanical_gate
-> verdict_critique -> rank -> select`) with stubbed reasoning, KB,
build-pipeline, and engine evaluator collaborators. The recorded
:class:`SmokeReport` is the regression fixture: subsequent commits must
not silently change its shape.

The function stays pure-Python: no native engine, no network, no real
build. It exercises the orchestrator's wiring + persistence end-to-end
so a regression in any node surfaces here. Native-backed paths are
exercised by `test_tester_native.py` / `test_kb_native.py`.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build_pipeline import (
    BuildArtifact,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from .hypothesize import HypothesizeDeps, HypothesizeResult, hypothesize
from .markdown_io import (
    Stage1Idea,
    Stage2Commitments,
    Stage3Files,
    serialize_stage1,
    serialize_stage2,
    serialize_stage3,
)
from .per_strategy_ledger import PerStrategyLedger
from .reasoning import HypothesisLoopConfig, ReasoningModel
from .types import BacktestMetrics, BacktestResult, RunnerVersion

# ---------------------------------------------------------------------------
# Smoke report — regression fixture surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmokeReport:
    """Recorded smoke-run summary used as a regression fixture.

    Field shape is part of the contract — CI compares the JSON
    serialization byte-for-byte (up to rounded floats). When intentionally
    changing the flow, regenerate via
    ``python -m strategy_gpt.smoke --write kb/fixtures/smoke_run.json``.
    """

    strategy: str
    termination_reason: str
    iterations: int
    backtests_consumed: int
    accepted_names: list[str]
    rejected_names: list[str]
    accepted_aggregate_scores: list[float]
    baseline_aggregate_score: float
    kb_citation_count: int
    persisted_decision_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "termination_reason": self.termination_reason,
            "iterations": self.iterations,
            "backtests_consumed": self.backtests_consumed,
            "accepted_names": list(self.accepted_names),
            "rejected_names": list(self.rejected_names),
            "accepted_aggregate_scores": [round(s, 6) for s in self.accepted_aggregate_scores],
            "baseline_aggregate_score": round(self.baseline_aggregate_score, 6),
            "kb_citation_count": self.kb_citation_count,
            "persisted_decision_count": self.persisted_decision_count,
        }


# ---------------------------------------------------------------------------
# Stubs — deterministic, offline, byte-stable
# ---------------------------------------------------------------------------


@dataclass
class _StubKbProvenance:
    source_id: str
    title: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    page: int | None = None


@dataclass
class _StubKbItem:
    chunk_id: str
    text: str
    score: float
    provenance: _StubKbProvenance


@dataclass
class _StubKbResult:
    items: list[_StubKbItem] = field(default_factory=list)


class _StubKb:
    """Canned KB returning two VXX-aligned citations."""

    def retrieve(self, query: str, k: int) -> Any:  # noqa: ANN401
        del query
        prov = _StubKbProvenance(
            source_id="starter-vol-regimes",
            title="Volatility Regimes and the VIX Term Structure",
            section="Empirical Properties",
            page=42,
        )
        items = [
            _StubKbItem(
                chunk_id="starter-vol-regimes::chunk::0",
                text="VXX loses value in contango; backwardation episodes reverse the roll yield.",
                score=0.91,
                provenance=prov,
            ),
            _StubKbItem(
                chunk_id="starter-vol-regimes::chunk::1",
                text="A vol regime classifier gives the strategy a discrete state to switch on.",
                score=0.83,
                provenance=prov,
            ),
        ]
        return _StubKbResult(items=items[:k])


_CANDIDATES: list[tuple[Stage1Idea, Stage2Commitments, Stage3Files]] = [
    (
        Stage1Idea(
            candidate_name="tighten_vol_lo",
            rationale=(
                "Diagnosis shows entries clustered near vol_lo; tightening the "
                "lower band should improve signal quality without changing the "
                "exit channel."
            ),
            expected_lift_confidence=0.65,
            expected_side_effects=["trade_count_changes_modestly"],
        ),
        Stage2Commitments(
            falsification={
                "primary": {
                    "metric": "sharpe",
                    "direction": "gt",
                    "delta_vs_baseline": 0.1,
                    "scope": {"kind": "aggregate"},
                },
                "guard_constraints": [
                    {
                        "metric": "max_drawdown",
                        "direction": "lte",
                        "delta_vs_baseline": 0.05,
                    }
                ],
            },
            param_intent={
                "added": [
                    {"name": "vol_lo", "kind": "f64", "min": 0.005, "max": 0.02, "default": 0.008}
                ],
                "kept": [],
                "removed": [],
            },
        ),
        Stage3Files(
            files={
                "Cargo.toml": '[package]\nname = "vxx_volatility_range"\nversion = "0.1.0"\n',
                "src/lib.rs": "// candidate body (smoke stub)\n",
                "params_schema.json": json.dumps(
                    {
                        "params": [
                            {"name": "vol_lo", "kind": "f64", "min": 0.005, "max": 0.02},
                        ]
                    }
                ),
            },
            deleted=[],
        ),
    ),
    (
        Stage1Idea(
            candidate_name="widen_vol_hi",
            rationale=(
                "Backwardation regimes are sparse but high-value; widening "
                "the upper threshold should capture more of them."
            ),
            expected_lift_confidence=0.55,
            expected_side_effects=["trade_count_up_modestly"],
        ),
        Stage2Commitments(
            falsification={
                "primary": {
                    "metric": "sharpe",
                    "direction": "gt",
                    "delta_vs_baseline": 0.08,
                    "scope": {"kind": "aggregate"},
                },
                "guard_constraints": [
                    {
                        "metric": "max_drawdown",
                        "direction": "lte",
                        "delta_vs_baseline": 0.05,
                    }
                ],
            },
            param_intent={
                "added": [
                    {"name": "vol_hi", "kind": "f64", "min": 0.03, "max": 0.07, "default": 0.05}
                ],
                "kept": [],
                "removed": [],
            },
        ),
        Stage3Files(
            files={
                "Cargo.toml": '[package]\nname = "vxx_volatility_range"\nversion = "0.1.0"\n',
                "src/lib.rs": "// candidate body 2 (smoke stub)\n",
                "params_schema.json": json.dumps(
                    {
                        "params": [
                            {"name": "vol_hi", "kind": "f64", "min": 0.03, "max": 0.07},
                        ]
                    }
                ),
            },
            deleted=[],
        ),
    ),
]


class _StubStageClient:
    """Cycles through pre-baked stage emissions per candidate."""

    def __init__(self) -> None:
        self._idx = 0

    def emit_stage(
        self,
        *,
        prompt: object,
        stage: int,
        model: object,
        **_: object,
    ) -> str:
        del prompt, model
        idea, commits, files = _CANDIDATES[self._idx % len(_CANDIDATES)]
        if stage == 1:
            return serialize_stage1(idea)
        if stage == 2:  # noqa: PLR2004 — stage constants are part of the contract
            return serialize_stage2(commits)
        # stage 3 — advance the candidate pointer once stage 3 is emitted
        text = serialize_stage3(files)
        self._idx += 1
        return text


class _StubBuildPipeline:
    """Lint always ok, build returns a deterministic synthetic artifact."""

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        del source, manifest
        return LintReport(ok=True, source_violations=[], manifest_violations=[])

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        del source
        del manifest
        artifact = BuildArtifact(
            key="smoke-stub-artifact",
            library_path="(smoke)/libstub.so",
            runner_version=RunnerVersion(major=0, minor=1, patch=0),
            source_size_bytes=0,
        )
        return BuildOutcome(kind=BuildOutcomeKind.COMPILED, artifact=artifact)


def _evaluate_fold(params: Mapping[str, Any], fold_idx: int) -> BacktestMetrics:
    """Deterministic surrogate metric surface.

    Peaks at ``vol_lo=0.008`` / ``vol_hi=0.05``. Returns a per-fold
    BacktestMetrics so the tester can compute per-fold scores + average
    metrics for side-effect flagging.
    """
    vol_lo = float(params.get("vol_lo", 0.01))
    vol_hi = float(params.get("vol_hi", 0.04))
    distance = abs(vol_lo - 0.008) * 50.0 + abs(vol_hi - 0.05) * 25.0
    sharpe = max(0.0, 1.5 - distance) + 0.01 * fold_idx
    return BacktestMetrics(
        sharpe=sharpe,
        sortino=sharpe * 1.1,
        profit_factor=1.2 + sharpe * 0.3,
        win_ratio=0.55,
        max_drawdown=0.08 + distance * 0.02,
        annualized_return=0.18,
        n_trades=120,
        avg_trade_length_bars=5.0,
    )


def _toy_baseline_result() -> BacktestResult:
    """Minimal `BacktestResult` for the diagnose step."""
    return BacktestResult.model_validate_json(
        """{
          "meta": {
            "strategy_artifact": "blake3:vxx",
            "dataset_manifest": "blake3:vxx-dataset",
            "seed": 42,
            "runner_version": {"major": 0, "minor": 1, "patch": 0}
          },
          "metrics": {
            "sharpe": 0.6,
            "sortino": 0.8,
            "profit_factor": 1.1,
            "win_ratio": 0.45,
            "max_drawdown": 0.18,
            "annualized_return": 0.08,
            "n_trades": 6,
            "avg_trade_length_bars": 4.0
          },
          "trades": [
            {"symbol":"VXX","entry_ts":"2024-03-01T00:00:00Z","exit_ts":"2024-03-05T00:00:00Z","side":"Short","size":100,"entry_price":20,"exit_price":18.8,"pnl":120,"fees":1,"reason_in":"contango_low_vol_entry","reason_out":"backwardation_exit","signals_at_entry":["vol_value","enter_short"]}
          ],
          "signals": [
            {"name":"vol_value","ts":"2024-03-01T00:00:00Z","value":0.009,"fired":true,"suppressed_by":null}
          ],
          "equity": [
            {"ts":"2024-03-01T00:00:00Z","equity":100000,"drawdown":0,"exposure":0},
            {"ts":"2024-04-08T00:00:00Z","equity":100030,"drawdown":-0.02,"exposure":0}
          ],
          "exec_log": [
            {"ts":"2024-03-01T00:00:00Z","event":"entry_skipped","details":{}}
          ],
          "regimes": [
            {"label":"low_vol","start":"2024-03-01T00:00:00Z","end":"2024-04-01T00:00:00Z"},
            {"label":"high_vol","start":"2024-04-01T00:00:00Z","end":"2024-05-01T00:00:00Z"}
          ],
          "stress": null,
          "sensitivity": null
        }"""
    )


def _baseline_files() -> dict[str, str]:
    return {
        "Cargo.toml": '[package]\nname = "vxx_volatility_range"\nversion = "0.1.0"\n',
        "src/lib.rs": "// baseline body (smoke)\n",
        "params_schema.json": json.dumps(
            {"params": [{"name": "vol_lo", "kind": "f64", "min": 0.005, "max": 0.02}]}
        ),
    }


# ---------------------------------------------------------------------------
# Smoke entry
# ---------------------------------------------------------------------------


_STRATEGY = "vxx_volatility_range"


def run_smoke(*, write_fixture_to: Path | None = None) -> SmokeReport:
    """Drive the rewritten hypothesis loop once and return a recorded report.

    Persists artifacts to an in-process tmpdir so the run leaves no
    on-disk footprint; ``persisted_decision_count`` confirms the
    persistence layer wrote the expected rows.
    """
    with tempfile.TemporaryDirectory(prefix="smoke-ledger-") as tmp:
        ledger_root = Path(tmp)
        ledger = PerStrategyLedger(ledger_root, _STRATEGY)
        baseline = _toy_baseline_result()
        baseline_files = _baseline_files()
        baseline_per_fold = [1.0, 1.05, 1.0]
        baseline_metrics = {"max_drawdown": 0.08, "n_trades": 100.0, "avg_trade_length_bars": 5.0}

        deps = HypothesizeDeps(
            kb=_StubKb(),
            stage_client=_StubStageClient(),
            build_pipeline=_StubBuildPipeline(),
            evaluate_fold=_evaluate_fold,
            prompt_api="(smoke-stub)",
            allowed_metrics=[
                "sharpe",
                "sortino",
                "profit_factor",
                "win_ratio",
                "max_drawdown",
                "annualized_return",
                "n_trades",
                "avg_trade_length_bars",
            ],
            baseline_result=baseline,
            baseline_files=baseline_files,
            baseline_params_schema={
                "params": [{"name": "vol_lo", "kind": "f64", "min": 0.005, "max": 0.02}]
            },
            baseline_per_fold_scores=baseline_per_fold,
            baseline_metrics=baseline_metrics,
            baseline_aggregate_score=sum(baseline_per_fold) / len(baseline_per_fold),
            objective_metric="sharpe",
            dataset_manifest_hash="smoke-dataset-manifest",
        )

        config = HypothesisLoopConfig.with_defaults(
            reasoning_model=ReasoningModel(provider="anthropic", model_id="smoke-stub"),
            target_candidates=2,
            iteration_budget=2,
            similarity_threshold=0.95,
        )

        result: HypothesizeResult = hypothesize(
            _STRATEGY,
            ledger=ledger,
            deps=deps,
            config=config,
            persist=True,
            max_backtests=200,
        )

        accepted_scores: list[float] = []
        for accepted in result.accepted:
            evidence = accepted.evidence or {}
            attempt = evidence.get("attempt_result") if isinstance(evidence, dict) else None
            if isinstance(attempt, dict) and "aggregate_score" in attempt:
                accepted_scores.append(float(attempt["aggregate_score"]))

        report = SmokeReport(
            strategy=_STRATEGY,
            termination_reason=result.termination_reason.value,
            iterations=result.iterations,
            backtests_consumed=result.backtests_consumed,
            accepted_names=[a.candidate.name for a in result.accepted],
            rejected_names=[r.candidate.name for r in result.rejected],
            accepted_aggregate_scores=accepted_scores,
            baseline_aggregate_score=deps.baseline_aggregate_score,
            kb_citation_count=len(result.state.get("kb_cites", [])),
            persisted_decision_count=len(result.persisted_decision_ids),
        )

    if write_fixture_to is not None:
        write_fixture_to.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n")

    return report


def _main() -> None:
    import argparse  # noqa: PLC0415 — CLI entry only

    parser = argparse.ArgumentParser(description="Run the hypothesis-loop smoke")
    parser.add_argument("--write", type=Path, default=None, help="Write the JSON fixture here")
    args = parser.parse_args()
    report = run_smoke(write_fixture_to=args.write)
    print(json.dumps(report.to_json(), indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    _main()


__all__ = ["SmokeReport", "run_smoke"]
