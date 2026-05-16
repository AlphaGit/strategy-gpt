"""Smoke-run regression test.

Asserts the end-to-end pipeline's recorded summary against the fixture in
``kb/fixtures/smoke_run.json``. Any change in the pipeline's emitted
shape — diagnosis fields, hypothesis names, optimizer best, rationale —
must be reflected in the fixture (run with ``python -m
strategy_gpt.smoke`` to regenerate).
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_gpt.smoke import run_smoke


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[2] / "kb" / "fixtures" / "smoke_run.json"


def test_smoke_report_matches_fixture() -> None:
    report = run_smoke()
    fixture = json.loads(_fixture_path().read_text())
    actual = report.to_json()
    # Round optimizer score for comparison stability (fixed inputs, but the
    # field is a float).
    actual["optimizer_best_score"] = round(actual["optimizer_best_score"], 6)
    fixture["optimizer_best_score"] = round(fixture["optimizer_best_score"], 6)
    assert actual == fixture


def test_smoke_report_has_expected_shape() -> None:
    report = run_smoke()
    assert report.diagnosis_summary["trade_count"] == 2
    assert "low_vol" in report.diagnosis_summary["regime_labels"]
    assert "high_vol" in report.diagnosis_summary["regime_labels"]
    assert report.accepted_hypotheses, "expected ≥1 accepted hypothesis"
    assert "rewrite_entry_logic" in report.rejected_hypotheses
    assert report.optimizer_trial_count == 12
    assert report.kb_citation_count == 2
    assert "Selected parameters" in report.rationale
