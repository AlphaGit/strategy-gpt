"""Persistence + replay tests (optimize-command tasks 6.x + 8.3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimization_ledger import (
    OptimizationLedger,
    build_replay_batch,
    find_trial,
    index_path,
    opt_dir_for,
    read_best,
    read_manifest,
    read_trials,
)
from strategy_gpt.types import EvaluationOutcome


def _write_spec(tmp_path: Path) -> Path:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    spec = f"""
artifact: {artifact}
strategy_label: stub
bars:
  dataset: deadbeef
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 7
    slice:
      start: 2020-01-01T00:00:00Z
      end: 2024-01-01T00:00:00Z
folds: {{ count: 2, scheme: rolling }}
optimize:
  method: recursive_grid
  seed: 1
  aggregator: mean
  space:
    x: {{ type: float, low: 0.0, high: 1.0 }}
  recursive_grid:
    resolution: 3
    top_k: 1
    depth: 2
    plateau_epsilon: 0.0001
  persist: {{ root: ./ledger, name: rt }}
parallelism: 1
"""
    path = tmp_path / "experiment.yaml"
    path.write_text(spec)
    return path


class _StubEngine:
    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}
        self._counter = 0
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(spec)
        self._counter += 1
        handle = f"job-{self._counter}"
        results = []
        for i, run in enumerate(spec["runs"]):
            x = float(run["params"]["x"])
            metrics = {"sharpe": 1.0 - (x - 0.4) ** 2}
            results.append({"status": "ok", "run_index": i, "result": {"metrics": metrics}})
        self._jobs[handle] = JobStatus(status="completed", results=results)
        return handle

    def poll(self, handle: str) -> JobStatus:
        return self._jobs[handle]

    def drop_handle(self, handle: str) -> bool:
        return self._jobs.pop(handle, None) is not None


def _accept_all(_objective: Mapping[str, Any], metrics: Mapping[str, Any]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True, score=float(metrics["sharpe"]), violations=[], soft_misses=[]
    )


def test_optimization_ledger_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)
    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    engine = _StubEngine()
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)
    writer.chunk_size = 4  # exercise more than one flush per round.

    result = runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=engine,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="round-trip",
        persist_writer=writer,
    )

    opt_dir = opt_dir_for(ledger_root, "round-trip")
    assert opt_dir.exists()
    manifest = read_manifest(opt_dir)
    assert manifest["status"] == "completed"
    assert manifest["trial_count"] == len(result.trial_rows)
    assert manifest["resolved_parallelism"] == 1

    trials = read_trials(opt_dir)
    assert len(trials) == len(result.trial_rows)
    disk_sorted = sorted(trials, key=lambda t: t.trial_id)
    mem_sorted = sorted(result.trial_rows, key=lambda t: t.trial_id)
    for disk, mem in zip(disk_sorted, mem_sorted, strict=True):
        assert disk.trial_id == mem.trial_id
        assert disk.phase == mem.phase
        assert disk.fold_index == mem.fold_index
        assert disk.params == mem.params

    best = read_best(opt_dir)
    assert best is not None
    final = best["final"]
    assert final is not None

    # SQLite index reflects the completed run.
    con = sqlite3.connect(index_path(ledger_root))
    try:
        row = con.execute("SELECT opt_id, status, trial_count FROM optimizations").fetchone()
    finally:
        con.close()
    assert row == ("round-trip", "completed", len(result.trial_rows))


def test_build_replay_batch_reconstructs_single_run_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)
    experiment = espec.load(spec_path)
    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    engine = _StubEngine()
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)

    runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=engine,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="replay",
        persist_writer=writer,
    )
    opt_dir = opt_dir_for(ledger_root, "replay")
    manifest = read_manifest(opt_dir)
    trial = find_trial(opt_dir, 0)
    assert trial is not None
    batch = build_replay_batch(manifest, trial)
    assert batch["strategy"] == "stub"
    assert batch["dataset"] == "deadbeef"
    assert batch["failure_mode"] == "abort"
    assert batch["parallelism"] == 1
    assert len(batch["runs"]) == 1
    run = batch["runs"][0]
    assert run["params"]["x"] == trial.params["x"]
    assert run["seed"] == trial.seed
    assert "start" in run["slice"]
