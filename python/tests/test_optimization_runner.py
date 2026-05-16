"""Per-fold orchestrator unit tests (optimize-command tasks 8.1 + 8.5 wiring)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.engine import JobStatus
from strategy_gpt.types import EvaluationOutcome


class _StubEngine:
    """Submit-batch stub that returns deterministic metrics per ``params``."""

    def __init__(self, metric_fn: Any) -> None:
        self.metric_fn = metric_fn
        self._jobs: dict[str, JobStatus] = {}
        self._counter = 0
        self.batches_submitted: list[dict[str, Any]] = []

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
        self.batches_submitted.append(spec)
        self._counter += 1
        handle = f"job-{self._counter}"
        results: list[dict[str, Any]] = []
        for i, run in enumerate(spec["runs"]):
            metrics = self.metric_fn(run["params"], run["slice"])
            results.append(
                {
                    "status": "ok",
                    "run_index": i,
                    "result": {"metrics": metrics},
                }
            )
        self._jobs[handle] = JobStatus(status="completed", results=results)
        return handle

    def poll(self, handle: str) -> JobStatus:
        return self._jobs[handle]

    def drop_handle(self, handle: str) -> bool:
        return self._jobs.pop(handle, None) is not None


def _write_spec(tmp_path: Path, *, optimize_method: str = "recursive_grid") -> Path:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    spec = f"""
artifact: {artifact}
strategy_label: stub
bars:
  dataset: deadbeef
runs:
  - params:
      base: 1
    modes:
      - {{ kind: plain }}
    seed: 7
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
    y:
      type: float
      low: 0.0
      high: 1.0
  recursive_grid:
    resolution: 4
    top_k: 1
    depth: 2
    plateau_epsilon: 0.0001
  persist:
    root: ./ledger
    name: stub-run
parallelism: 1
"""
    path = tmp_path / "experiment.yaml"
    path.write_text(spec)
    return path


def _accept_all_score(
    _objective: Mapping[str, Any], metrics: Mapping[str, Any]
) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True,
        score=float(metrics.get("sharpe", float("-inf"))),
        violations=[],
        soft_misses=[],
    )


def test_run_optimization_picks_best_fold_winner_on_synthetic_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)

    # Surface maximum at (0.4, 0.7); deterministic by params only.
    def metric_fn(params: Mapping[str, Any], _slice: Mapping[str, Any]) -> dict[str, float]:
        x = float(params["x"])
        y = float(params["y"])
        return {"sharpe": 1.0 - (x - 0.4) ** 2 - (y - 0.7) ** 2}

    monkeypatch.setattr(runner, "evaluate_spec", _accept_all_score)
    engine = _StubEngine(metric_fn)
    result = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=engine,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="test-opt",
    )
    assert result.final is not None
    assert abs(result.final.params["x"] - 0.4) < 0.15
    assert abs(result.final.params["y"] - 0.7) < 0.15
    # Trials: per-fold rounds + cross-validation phase. 4 folds x (4*4 res^2)
    # x 2 rounds = 128 train trials, plus 4*4 = 16 cross-validation.
    train_rows = [r for r in result.trial_rows if r.phase.startswith("train_fold_")]
    cross_rows = [r for r in result.trial_rows if r.phase.startswith("final_cross_")]
    assert len(train_rows) == 4 * 4 * 4 * 2  # 4 folds x res^2 x depth.  noqa: PLR2004
    assert len(cross_rows) == 4 * 4  # F x F.  noqa: PLR2004


def test_run_optimization_cross_validation_picks_lower_variance_on_tie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = _write_spec(tmp_path, optimize_method="grid")
    # Strip method-specific knobs so grid uses defaults.
    raw = spec_path.read_text().replace(
        "method: grid",
        "method: grid\n  grid:\n    resolution: 2",
    )
    spec_path.write_text(raw)
    experiment = espec.load(spec_path)

    # Two candidates tie on mean score but one has lower variance.
    def metric_fn(params: Mapping[str, Any], slice_: Mapping[str, Any]) -> dict[str, float]:
        # Deterministic differentiator: candidate identity by (x, y) bucket.
        x = float(params["x"])
        if x < 0.5:  # "stable" candidate, identical OOS each fold.  noqa: PLR2004
            return {"sharpe": 1.0}
        # "volatile" candidate, mean 1.0 but big swings per fold's slice start year.
        start = slice_["start"]
        return {"sharpe": 0.5 if "2021" in start or "2023" in start else 1.5}

    monkeypatch.setattr(runner, "evaluate_spec", _accept_all_score)
    engine = _StubEngine(metric_fn)
    result = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=engine,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="tie-opt",
    )
    # The "stable" winner (x < 0.5) should be picked under tie-break by variance.
    assert result.final is not None
    assert result.final.params["x"] < 0.5
