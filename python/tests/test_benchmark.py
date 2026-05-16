"""Benchmark predictor tests (optimize-command tasks 4.x + 8.2)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from strategy_gpt import benchmark as bench
from strategy_gpt import experiment_spec as espec
from strategy_gpt.engine import JobStatus


def _write_spec(tmp_path: Path) -> Path:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    spec = f"""
artifact: {artifact}
bars:
  dataset: deadbeef
runs:
  - params: {{}}
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
  method: recursive_grid
  seed: 1
  aggregator: mean
  space:
    x: {{ type: float, low: 0.0, high: 1.0 }}
    y: {{ type: float, low: 0.0, high: 1.0 }}
  recursive_grid:
    resolution: 5
    top_k: 1
    depth: 3
  persist:
    root: ./ledger
    name: bench
parallelism: 1
"""
    path = tmp_path / "experiment.yaml"
    path.write_text(spec)
    return path


class _ConstantEngine:
    """Engine stub that returns fixed per-run wall time."""

    def __init__(self, per_run_secs: float) -> None:
        self.per_run_secs = per_run_secs
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
        time.sleep(self.per_run_secs * len(spec["runs"]))
        results = [
            {"status": "ok", "run_index": i, "result": {"metrics": {"sharpe": 1.0}}}
            for i in range(len(spec["runs"]))
        ]
        self._jobs[handle] = JobStatus(status="completed", results=results)
        return handle

    def poll(self, handle: str) -> JobStatus:
        return self._jobs[handle]

    def drop_handle(self, handle: str) -> bool:
        return self._jobs.pop(handle, None) is not None


def test_planned_run_count_recursive_grid(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)
    assert experiment.optimize is not None
    # res^D x depth x folds + folds^2  =  5^2 x 3 x 4 + 4^2 = 316
    assert bench.planned_run_count(experiment.optimize, folds_count=4) == 316


def test_run_benchmark_predicts_within_band(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)
    engine = _ConstantEngine(per_run_secs=0.005)
    report = bench.run_benchmark(
        experiment=experiment,
        engine=engine,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        sample_size=2,
    )
    assert report.planned_total_runs == 316
    assert report.predicted_wall_secs_low < report.predicted_wall_secs_high
    # Predicted ledger footprint scales linearly with planned runs.
    assert report.predicted_ledger_bytes == 316 * 200


def test_planned_run_count_grid_uses_step(tmp_path: Path) -> None:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    spec_text = f"""
artifact: {artifact}
bars: {{ dataset: dead }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 0
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 4, scheme: rolling }}
optimize:
  method: grid
  seed: 0
  aggregator: mean
  space:
    a: {{ type: float, low: 0.0, high: 1.0, step: 0.25 }}
    b: {{ type: int, low: 0, high: 4, step: 1 }}
  grid: {{ resolution: 5 }}
  persist: {{ root: ./ledger, name: g }}
parallelism: 1
"""
    path = tmp_path / "experiment.yaml"
    path.write_text(spec_text)
    experiment = espec.load(path)
    assert experiment.optimize is not None
    # a: low=0 high=1 step=0.25 → 5 points; b: 0..4 step 1 → 5 points. F=4.
    # 5*5*4 + 4*4 = 100 + 16 = 116
    assert bench.planned_run_count(experiment.optimize, folds_count=4) == 116
