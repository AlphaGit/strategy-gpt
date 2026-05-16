"""End-to-end selection integration through the per-fold runner.

These tests stub the engine (no Rust dependency) so they exercise the
full selection-layer path: candidate construction from cross-validation
outcomes, PBO/DSR/sensitivity computation, and best-trial reconciliation.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimization_runner import SelectionOverrides
from strategy_gpt.selection import SelectionStatus
from strategy_gpt.types import EvaluationOutcome


class _StubEngine:
    def __init__(self, metric_fn: Any) -> None:
        self.metric_fn = metric_fn
        self._jobs: dict[str, JobStatus] = {}
        self._counter = 0

    def submit_batch(
        self,
        artifact_path: Any,
        bars: Any,
        spec: dict[str, Any],
        dataset_manifest: str,
        *,
        run_id: str | None = None,
    ) -> str:
        del artifact_path, bars, dataset_manifest, run_id
        self._counter += 1
        handle = f"job-{self._counter}"
        results: list[dict[str, Any]] = []
        for i, run in enumerate(spec["runs"]):
            metrics = self.metric_fn(run["params"], run["slice"])
            results.append({"status": "ok", "run_index": i, "result": {"metrics": metrics}})
        self._jobs[handle] = JobStatus(status="completed", results=results)
        return handle

    def poll(self, handle: str) -> JobStatus:
        return self._jobs[handle]

    def drop_handle(self, handle: str) -> bool:
        return self._jobs.pop(handle, None) is not None


def _accept_all(_obj: Mapping[str, Any], metrics: Mapping[str, Any]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True,
        score=float(metrics.get("sharpe", float("-inf"))),
        violations=[],
        soft_misses=[],
    )


def _write_spec(tmp_path: Path, *, optimize_method: str = "grid") -> Path:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    spec = f"""
artifact: {artifact}
strategy_label: stub
bars:
  dataset: deadbeef
runs:
  - params: {{}}
    modes:
      - {{ kind: plain }}
    seed: 1
    slice:
      start: 2020-01-01T00:00:00Z
      end: 2024-01-01T00:00:00Z
folds:
  count: 4
  scheme: rolling
optimize:
  method: {optimize_method}
  seed: 1
  aggregator: mean
  space:
    x:
      type: float
      low: 0.0
      high: 1.0
  grid:
    resolution: 4
  persist:
    root: ./ledger
    name: stub-run
parallelism: 1
"""
    path = tmp_path / "experiment.yaml"
    path.write_text(spec)
    return path


def test_runner_populates_selection_with_all_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: selection layer runs, decision + PBO + DSR + sensitivity all present."""
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)

    def metric_fn(params: Mapping[str, Any], _slice: Mapping[str, Any]) -> dict[str, float]:
        x = float(params["x"])
        return {"sharpe": 1.0 - (x - 0.5) ** 2, "n_trades": 100}

    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    eng = _StubEngine(metric_fn)
    result = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="test-opt",
    )
    assert result.selection is not None
    sel = result.selection
    assert sel.status == SelectionStatus.ACCEPTED
    assert sel.best_trial_id is not None
    assert sel.pbo.n_folds == 4
    assert len(sel.candidate_scores) == len(result.cross_validation)
    for cs in sel.candidate_scores:
        assert cs.dsr.dsr >= 0.0
        assert cs.sensitivity.robust_score == pytest.approx(
            cs.sensitivity.neighborhood_mean - cs.sensitivity.neighborhood_std,
            rel=1e-6,
            abs=1e-6,
        )
    # methodology citations present
    assert "pbo" in sel.methodology
    assert "dsr" in sel.methodology
    assert "sensitivity" in sel.methodology
    # final aligns with selection's best
    assert result.final is not None


def test_runner_force_override_records_pbo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A perfect-overfit fold pattern is normally rejected; --force publishes a best."""
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)

    def metric_fn(params: Mapping[str, Any], slice_: Mapping[str, Any]) -> dict[str, float]:
        # Each candidate dominates in exactly one fold and is poor elsewhere.
        x = float(params["x"])
        # Bucket x to one of 4 indices.
        bucket = min(3, max(0, int(x * 4)))
        # Bucket fold by slice start year.
        start = slice_["start"]
        year_to_fold = {"2020": 0, "2021": 1, "2022": 2, "2023": 3}
        fold = next((v for k, v in year_to_fold.items() if k in start), 0)
        return {"sharpe": 10.0 if bucket == fold else -1.0, "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    eng = _StubEngine(metric_fn)
    rejected = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="test-overfit",
    )
    assert rejected.selection is not None
    assert rejected.selection.status == SelectionStatus.REJECTED_PBO
    assert rejected.final is None
    assert rejected.selection.would_have_picked_trial_id is not None

    eng2 = _StubEngine(metric_fn)
    forced = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng2,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="test-overfit-forced",
        selection_overrides=SelectionOverrides(force=True),
    )
    assert forced.selection is not None
    assert forced.selection.status == SelectionStatus.ACCEPTED
    assert forced.selection.force_override is True
    # PBO is still computed and surfaced under --force.
    assert forced.selection.pbo.pbo > 0.5
    assert forced.final is not None
