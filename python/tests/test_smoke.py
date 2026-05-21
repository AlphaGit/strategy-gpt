"""Smoke-run regression test for the rewritten hypothesis loop.

Compares the recorded :class:`SmokeReport` against the fixture in
``kb/fixtures/smoke_run.json``. Any intentional flow change requires
regenerating the fixture via
``python -m strategy_gpt.smoke --write kb/fixtures/smoke_run.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

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
