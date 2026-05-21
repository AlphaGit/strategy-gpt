"""Smoke-run regression test for the rewritten hypothesis loop.

Compares the recorded :class:`SmokeReport` against the fixture in
``kb/fixtures/smoke_run.json``. Any intentional flow change requires
regenerating the fixture via
``python -m strategy_gpt.smoke --write kb/fixtures/smoke_run.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_gpt.cli import _find_decision_record
from strategy_gpt.per_strategy_ledger import PerStrategyLedger
from strategy_gpt.smoke import run_smoke


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[2] / "kb" / "fixtures" / "smoke_run.json"


def test_smoke_report_matches_fixture() -> None:
    actual = run_smoke().to_json()
    fixture = json.loads(_fixture_path().read_text())
    assert actual == fixture


def test_smoke_report_has_expected_shape() -> None:
    report = run_smoke()
    assert report.strategy == "vxx_volatility_range"
    assert report.kb_citation_count == 2
    assert report.persisted_decision_count == len(report.accepted_names) + len(
        report.rejected_names
    )
    assert report.accepted_names, "expected at least one accepted candidate"
    # baseline aggregate is computed from the three-fold baseline scores
    assert abs(report.baseline_aggregate_score - 1.016667) < 1e-3
    # winning candidate should beat baseline (mini-optimize peaks at vol_lo=0.008)
    assert max(report.accepted_aggregate_scores) > report.baseline_aggregate_score


def test_smoke_with_ledger_root_persists_replayable_decision(tmp_path: Path) -> None:
    """``--ledger-root`` keeps decision rows so replay tooling can find them.

    Drives the hypothesize-loop tutorial path: the smoke driver writes
    a per-strategy ledger to ``tmp_path``, then ``_find_decision_record``
    (the CLI's lookup used by ``hypothesis replay``/``diff``) resolves
    at least one recorded decision id.
    """
    ledger_root = tmp_path / "ledger"
    report = run_smoke(ledger_root=ledger_root)

    assert report.persisted_decision_count >= 1
    ledger = PerStrategyLedger(ledger_root, report.strategy)
    decision_ids = [d.id for d in ledger.decisions_iter()]
    assert decision_ids, "expected at least one decision row on disk"

    name, _, decision, hypothesis = _find_decision_record(
        ledger_root, report.strategy, decision_ids[0]
    )
    assert name == report.strategy
    assert decision.id == decision_ids[0]
    assert hypothesis.id == decision.hypothesis_id
