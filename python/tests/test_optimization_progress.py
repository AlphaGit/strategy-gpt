"""Unit tests for the central optimization progress reporter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt.optimization_progress import (
    StderrProgressRenderer,
    TeePersistWriter,
)
from strategy_gpt.optimization_runner import (
    CrossValidationOutcome,
    OptimizationResult,
    TrialRow,
)


class _SpyWriter:
    """Captures every persist-writer call for transparency assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self, **kw: Any) -> None:
        self.calls.append(("start", dict(kw)))

    def emit_row(self, row: TrialRow) -> None:
        self.calls.append(("emit_row", {"row": row}))

    def flush(self) -> None:
        self.calls.append(("flush", {}))

    def finish(self, result: OptimizationResult) -> None:
        self.calls.append(("finish", {"result": result}))


def _row(
    *,
    trial_id: int,
    phase: str,
    score: float,
    primary: float,
    accepted: bool = True,
) -> TrialRow:
    return TrialRow(
        trial_id=trial_id,
        round=0,
        phase=phase,
        fold_index=int(phase.rsplit("_", 1)[-1]) if phase[-1].isdigit() else 0,
        params={"x": 0.5, "y": 1},
        seed=7,
        metrics={"sharpe": primary, "n_trades": 12},
        score=score,
        accepted=accepted,
        reject_reason="" if accepted else "constraint_violation",
        wall_secs=0.1,
    )


def _empty_result() -> OptimizationResult:
    return OptimizationResult(
        opt_id="test",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        folds=[],
        fold_winners=[],
        cross_validation=[],
        final=None,
        trial_rows=[],
        resolved_parallelism=1,
        seed=7,
    )


def test_renderer_prints_only_new_best_during_train_phase(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = StderrProgressRenderer()
    r.on_trial(_row(trial_id=0, phase="train_fold_0", score=1.0, primary=1.1))
    r.on_trial(_row(trial_id=1, phase="train_fold_0", score=0.5, primary=0.6))  # not best
    r.on_trial(_row(trial_id=2, phase="train_fold_0", score=2.0, primary=1.8))  # new best
    r.on_trial(_row(trial_id=3, phase="train_fold_0", score=1.5, primary=1.4))  # not best
    r.on_phase_flush()

    err = capsys.readouterr().err
    assert "trial #0" in err
    assert "trial #1" not in err
    assert "trial #2" in err
    assert "trial #3" not in err
    assert "train_fold_0: 4 trial(s)" in err
    assert "best sharpe=1.8000" in err


def test_renderer_resets_best_on_phase_transition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = StderrProgressRenderer()
    r.on_trial(_row(trial_id=0, phase="train_fold_0", score=5.0, primary=2.0))
    r.on_trial(_row(trial_id=1, phase="train_fold_1", score=1.0, primary=0.5))
    r.on_phase_flush()

    err = capsys.readouterr().err
    # Both trials emit a "new best" line because each opens a fresh phase.
    assert "trial #0" in err
    assert "trial #1" in err
    assert "━━━ train_fold_0 ━━━" in err
    assert "━━━ train_fold_1 ━━━" in err


def test_renderer_suppresses_cross_validation_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = StderrProgressRenderer()
    r.on_trial(_row(trial_id=0, phase="final_cross_0", score=1.0, primary=1.0))
    r.on_trial(_row(trial_id=1, phase="final_cross_1", score=2.0, primary=2.0))
    err = capsys.readouterr().err
    assert err == ""


def test_renderer_on_finish_prints_cross_validation_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = StderrProgressRenderer()
    cv0 = CrossValidationOutcome(
        fold_index=0,
        params={"x": 0.5},
        oos_metrics=[],
        aggregate_metrics={"sharpe": 1.42},
        aggregate_score=1.30,
        aggregate_accepted=True,
        aggregate_reject_reason="",
        score_variance=0.01,
    )
    cv1 = CrossValidationOutcome(
        fold_index=1,
        params={"x": 0.7},
        oos_metrics=[],
        aggregate_metrics={"sharpe": 0.5},
        aggregate_score=0.4,
        aggregate_accepted=False,
        aggregate_reject_reason="fold_0:pf_below_threshold",
        score_variance=0.10,
    )
    result = OptimizationResult(
        opt_id="test",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        folds=[],
        fold_winners=[],
        cross_validation=[cv0, cv1],
        final=cv0,
        trial_rows=[],
        resolved_parallelism=1,
        seed=7,
    )
    r.on_finish(result)
    err = capsys.readouterr().err
    assert "cross_validation (2 winner(s))" in err
    assert "winner 0 (fold_0): sharpe=1.4200" in err
    assert "✓ accepted" in err
    assert "winner 1 (fold_1)" in err
    assert "✗ rejected: fold_0:pf_below_threshold" in err
    assert "final pick: fold_0" in err


def test_renderer_unaccepted_trials_do_not_become_best(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = StderrProgressRenderer()
    r.on_trial(_row(trial_id=0, phase="train_fold_0", score=99.0, primary=9.9, accepted=False))
    r.on_trial(_row(trial_id=1, phase="train_fold_0", score=1.5, primary=1.5))
    r.on_phase_flush()
    err = capsys.readouterr().err
    assert "trial #0" not in err
    assert "trial #1" in err
    assert "best sharpe=1.5000" in err


def test_tee_writer_forwards_every_call_to_inner(tmp_path: Path) -> None:
    inner = _SpyWriter()
    renderer = StderrProgressRenderer()
    tee = TeePersistWriter(inner, renderer)

    row = _row(trial_id=0, phase="train_fold_0", score=1.0, primary=1.1)
    tee.emit_row(row)
    tee.flush()
    tee.finish(_empty_result())

    kinds = [name for name, _ in inner.calls]
    assert kinds == ["emit_row", "flush", "finish"]
    assert inner.calls[0][1]["row"] is row


def test_tee_writer_preserves_inner_writer_payload() -> None:
    """The tee must not mutate or replace TrialRow / OptimizationResult."""
    inner = _SpyWriter()
    tee = TeePersistWriter(inner, StderrProgressRenderer())
    row = _row(trial_id=99, phase="train_fold_3", score=2.0, primary=1.5)
    tee.emit_row(row)
    forwarded = inner.calls[-1][1]["row"]
    assert forwarded is row
    assert forwarded.params == {"x": 0.5, "y": 1}
    assert forwarded.metrics == {"sharpe": 1.5, "n_trades": 12}
