"""End-to-end smoke run: data → engine → ledger → KB-aware hypothesis loop
→ tester → engine → verdict.

One pass through every public surface, stubbed where running against the
real LLM / native engine would slow down CI but real on the data shapes
and module wiring. The recorded :class:`SmokeReport` is the regression
fixture: subsequent commits must not silently change the shape or content
of this report. The reference VXX strategy crate (`crates/vxx-strategy`)
is the strategy under test; its objective spec lives at
`crates/vxx-strategy/objective.yaml`.

The function is intentionally pure-Python and free of native-extension
imports so it runs in any developer environment. Native-backed paths are
exercised separately by `test_tester_native.py` / `test_kb_native.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .diagnose import Diagnosis, diagnose
from .hypothesis_loop import (
    HypothesisCandidate,
    HypothesisLoopState,
    KbCitation,
    PriorDecision,
)
from .kb_query import kb_query_node
from .nodes import CritiqueOutcome, run_inner_loop
from .optimizer import GridSearcher, OptimizerResult, optimize
from .rationale import generate_rationale
from .reasoning import HypothesisLoopConfig, ReasoningModel
from .types import BacktestResult, EvaluationOutcome

_LIFT_CONFIDENCE_FLOOR = 0.4
_MAX_DRAWDOWN_HARD_CAP = 0.2


@dataclass(frozen=True)
class SmokeReport:
    """Recorded smoke-run summary used as a regression fixture."""

    diagnosis_summary: dict[str, Any]
    accepted_hypotheses: list[str]
    rejected_hypotheses: list[str]
    optimizer_best_params: dict[str, Any]
    optimizer_best_score: float
    optimizer_trial_count: int
    rationale: str
    kb_citation_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "diagnosis_summary": self.diagnosis_summary,
            "accepted_hypotheses": self.accepted_hypotheses,
            "rejected_hypotheses": self.rejected_hypotheses,
            "optimizer_best_params": self.optimizer_best_params,
            "optimizer_best_score": self.optimizer_best_score,
            "optimizer_trial_count": self.optimizer_trial_count,
            "rationale": self.rationale,
            "kb_citation_count": self.kb_citation_count,
        }


@dataclass
class _StubKbItem:
    chunk_id: str
    text: str
    score: float
    provenance: Any


@dataclass
class _StubKbProvenance:
    source_id: str
    title: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    page: int | None = None


@dataclass
class _StubKbResult:
    items: list[_StubKbItem]


class _StubKbClient:
    """Mimics `strategy_gpt.kb.KnowledgeBase.retrieve`; returns canned VXX-
    aligned citations so the smoke run resembles the production wiring
    without standing up the native extension."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, k: int) -> _StubKbResult:
        self.calls.append((query, k))
        prov = _StubKbProvenance(
            source_id="starter-vol-regimes",
            title="Volatility Regimes and the VIX Term Structure",
            author="strategy-gpt starter corpus",
            year=2026,
            section="Empirical Properties of the VIX Term Structure",
        )
        items = [
            _StubKbItem(
                chunk_id="starter-vol-regimes::chunk::0",
                text=(
                    "VXX loses value in contango; backwardation episodes are short, "
                    "bursty, and reverse the roll yield."
                ),
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


class _StubReasoningClient:
    """Canned hypothesis generator + critic. Deterministic so the smoke
    report is a byte-stable regression fixture."""

    def generate(
        self,
        *,
        diagnosis: Diagnosis,
        kb_cites: list[KbCitation],
        prior_decisions: list[PriorDecision],
        n: int,
        model: object,
    ) -> list[HypothesisCandidate]:
        del diagnosis, prior_decisions, model  # smoke run uses fixed candidates
        return [
            HypothesisCandidate(
                name="tighten_vol_lo",
                target_metric="sharpe",
                falsification={"op": ">=", "threshold": 1.0, "metric": "sharpe"},
                proposed_change={"diffs": [{"param": "vol_lo", "from": 0.01, "to": 0.008}]},
                kb_cites=list(kb_cites[:2]),
                estimated_lift_confidence=0.65,
            ),
            HypothesisCandidate(
                name="widen_vol_hi",
                target_metric="sharpe",
                falsification={"op": ">=", "threshold": 1.0, "metric": "sharpe"},
                proposed_change={"diffs": [{"param": "vol_hi", "from": 0.04, "to": 0.05}]},
                kb_cites=list(kb_cites[:1]),
                estimated_lift_confidence=0.55,
            ),
            HypothesisCandidate(
                name="rewrite_entry_logic",
                target_metric="sharpe",
                falsification={"op": ">=", "threshold": 1.0, "metric": "sharpe"},
                proposed_change={"source": "// new entry logic", "manifest": ""},
                kb_cites=[],
                estimated_lift_confidence=0.30,
            ),
        ][:n]

    def critique(
        self,
        *,
        candidate: HypothesisCandidate,
        prior_decisions: list[PriorDecision],
        diagnosis: Diagnosis | None,
        model: object,
    ) -> CritiqueOutcome:
        del prior_decisions, diagnosis, model
        if candidate.estimated_lift_confidence < _LIFT_CONFIDENCE_FLOOR:
            return CritiqueOutcome(
                accept=False,
                rationale=(
                    "Estimated lift below confidence floor; logic rewrites require "
                    "additional KB-grounded evidence before recompile."
                ),
                evidence=None,
            )
        return CritiqueOutcome(
            accept=True,
            rationale=(
                "Diff is consistent with starter-corpus guidance on contango vs "
                "backwardation thresholds and remains within the strategy's risk envelope."
            ),
            evidence={"kb_cites": [c.model_dump(mode="json") for c in candidate.kb_cites]},
        )


def _toy_backtest_result() -> BacktestResult:
    """Synthetic `BacktestResult` shaped after the VXX strategy's output.

    Hard-coded values so the smoke report's `diagnosis_summary` is stable
    across runs. The shape is what matters — the diagnose node walks
    trades / regimes / signals / exec_log and we want each branch populated.
    """
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
            {
              "symbol": "VXX",
              "entry_ts": "2024-03-01T00:00:00Z",
              "exit_ts": "2024-03-05T00:00:00Z",
              "side": "Short",
              "size": 100.0,
              "entry_price": 20.0,
              "exit_price": 18.8,
              "pnl": 120.0,
              "fees": 1.0,
              "reason_in": "contango_low_vol_entry",
              "reason_out": "backwardation_exit",
              "signals_at_entry": ["vol_value", "enter_short"]
            },
            {
              "symbol": "VXX",
              "entry_ts": "2024-04-01T00:00:00Z",
              "exit_ts": "2024-04-08T00:00:00Z",
              "side": "Short",
              "size": 100.0,
              "entry_price": 19.0,
              "exit_price": 19.9,
              "pnl": -90.0,
              "fees": 1.0,
              "reason_in": "contango_low_vol_entry",
              "reason_out": "backwardation_exit",
              "signals_at_entry": ["vol_value", "enter_short"]
            }
          ],
          "signals": [
            {
              "name": "vol_value",
              "ts": "2024-03-01T00:00:00Z",
              "value": 0.009,
              "fired": true,
              "suppressed_by": null
            },
            {
              "name": "enter_short",
              "ts": "2024-03-01T00:00:00Z",
              "value": 0.009,
              "fired": true,
              "suppressed_by": null
            },
            {
              "name": "hold",
              "ts": "2024-03-02T00:00:00Z",
              "value": 0.02,
              "fired": false,
              "suppressed_by": "threshold_band"
            }
          ],
          "equity": [
            {"ts": "2024-03-01T00:00:00Z", "equity": 100000.0, "drawdown": 0.0, "exposure": 0.0},
            {"ts": "2024-04-08T00:00:00Z", "equity": 100030.0, "drawdown": 0.02, "exposure": 0.0}
          ],
          "exec_log": [
            {"ts": "2024-03-01T00:00:00Z", "event": "entry_skipped", "details": {}},
            {"ts": "2024-03-02T00:00:00Z", "event": "filter_blocked", "details": {}}
          ],
          "regimes": [
            {"label": "low_vol", "start": "2024-03-01T00:00:00Z", "end": "2024-04-01T00:00:00Z"},
            {"label": "high_vol", "start": "2024-04-01T00:00:00Z", "end": "2024-05-01T00:00:00Z"}
          ],
          "stress": null,
          "sensitivity": null
        }"""
    )


def _score_fn(metrics: dict[str, float]) -> EvaluationOutcome:
    sharpe = metrics.get("sharpe", 0.0)
    drawdown = metrics.get("max_drawdown", 1.0)
    violations: list[str] = []
    if drawdown > _MAX_DRAWDOWN_HARD_CAP:
        violations.append("max_drawdown > 0.20")
    accepted = sharpe >= 1.0 and not violations
    return EvaluationOutcome(accepted=accepted, score=sharpe, violations=violations, soft_misses=[])


def _evaluate_fn(params: dict[str, Any]) -> dict[str, float]:
    """Deterministic surrogate for a fold-based batch.

    Score peaks at vol_lo=0.008, vol_hi=0.05; falls off with distance. The
    optimizer should pick the centre of the grid given a wide enough net.
    """
    vol_lo = float(params.get("vol_lo", 0.01))
    vol_hi = float(params.get("vol_hi", 0.04))
    centre_lo, centre_hi = 0.008, 0.05
    distance = abs(vol_lo - centre_lo) * 100.0 + abs(vol_hi - centre_hi) * 100.0
    sharpe = max(0.0, 1.5 - distance)
    max_dd = 0.1 + distance * 0.5
    return {"sharpe": sharpe, "max_drawdown": max_dd, "profit_factor": 1.0 + sharpe * 0.5}


def run_smoke(*, write_fixture_to: Path | None = None) -> SmokeReport:
    """Drive the full pipeline once and return a recorded report."""
    # 1. "Data fetch" + initial backtest result — represented by the toy
    #    BacktestResult above. Production: data_gateway.fetch + engine.submit.
    result = _toy_backtest_result()

    # 2. Diagnose the result.
    diagnosis = diagnose(result)

    # 3. KB-aware hypothesis loop. State starts with the diagnosis attached,
    #    kb_query attaches citations, then the inner loop runs.
    state = HypothesisLoopState(diagnosis=diagnosis)
    kb_client: Any = _StubKbClient()
    state = kb_query_node(state, client=kb_client, k=2)

    # Explicit (anthropic-labelled) stub model so the smoke runs without API
    # keys configured. The reasoning client is local-only; the label just keeps
    # the `Provider` Literal happy.
    config = HypothesisLoopConfig.with_defaults(
        reasoning_model=ReasoningModel(provider="anthropic", model_id="smoke-stub"),
        target_candidates=3,
        iteration_budget=2,
        similarity_threshold=0.95,
    )
    client = _StubReasoningClient()
    fixed_now = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    state = run_inner_loop(state, client=client, config=config, now=fixed_now)

    # 4. Optimizer + rationale over the leading accepted hypothesis (proxy
    #    for "tester accepts → optimize the new parameter region").
    searcher = GridSearcher(
        grid={
            "vol_lo": [0.006, 0.008, 0.010, 0.012],
            "vol_hi": [0.040, 0.050, 0.060],
        }
    )
    opt_result: OptimizerResult = optimize(searcher, _evaluate_fn, _score_fn)
    rationale = generate_rationale(
        opt_result,
        citations=state.kb_cites,
        kb_client=kb_client,
        strategy_name="vxx_volatility_range",
    )

    report = SmokeReport(
        diagnosis_summary={
            "trade_count": diagnosis.trade_stats.n_total,
            "win_rate": diagnosis.trade_stats.win_rate,
            "regime_labels": sorted({r.label for r in diagnosis.regime_performance}),
            "exec_log_kinds": sorted(diagnosis.exec_log_summary.keys()),
        },
        accepted_hypotheses=[a.candidate.name for a in state.accepted],
        rejected_hypotheses=[r.candidate.name for r in state.rejected],
        optimizer_best_params=dict(opt_result.best.params) if opt_result.best else {},
        optimizer_best_score=opt_result.best.outcome.score if opt_result.best else float("nan"),
        optimizer_trial_count=len(opt_result.trials),
        rationale=rationale,
        kb_citation_count=len(state.kb_cites),
    )

    if write_fixture_to is not None:
        write_fixture_to.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True))

    return report


__all__ = ["SmokeReport", "run_smoke"]
