"""Post-hoc reselection tests (require pyarrow for the parquet path)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pyarrow")

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimization_ledger import OptimizationLedger, reselect
from strategy_gpt.optimization_runner import SelectionOverrides
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
        results = [
            {
                "status": "ok",
                "run_index": i,
                "result": {"metrics": self.metric_fn(r["params"], r["slice"])},
            }
            for i, r in enumerate(spec["runs"])
        ]
        self._jobs[handle] = JobStatus(status="completed", results=results)
        return handle

    def poll(self, handle: str) -> JobStatus:
        return self._jobs[handle]

    def drop_handle(self, handle: str) -> bool:
        return self._jobs.pop(handle, None) is not None


def _accept_all(_o: Mapping[str, Any], m: Mapping[str, Any]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True, score=float(m.get("sharpe", float("-inf"))), violations=[], soft_misses=[]
    )


def _spec(tmp_path: Path) -> Path:
    artifact = tmp_path / "fake.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "experiment.yaml"
    p.write_text(f"""
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
  method: grid
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
    name: stub
parallelism: 1
""")
    return p


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metric_fn: Any, opt_id: str) -> Path:
    experiment = espec.load(_spec(tmp_path))
    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)
    eng = _StubEngine(metric_fn)
    runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id=opt_id,
        persist_writer=writer,
    )
    return ledger_root / "optimizations" / opt_id


def test_e2e_best_json_has_selection_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def f(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        return {"sharpe": 1.0 - (x - 0.5) ** 2, "n_trades": 100}

    opt_dir = _run(tmp_path, monkeypatch, f, "opt-e2e")
    best = json.loads((opt_dir / "best.json").read_text())
    assert "decision" in best
    assert "pbo" in best
    assert "deflated_sharpe" in best
    assert "sensitivity_score" in best
    assert "selection_methodology" in best
    assert best["decision"]["status"] in ("accepted", "rejected_pbo", "rejected_constraint")
    manifest = json.loads((opt_dir / "manifest.json").read_text())
    assert "selection_methodology" in manifest


def test_reselect_byte_identical_with_same_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def f(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        return {"sharpe": 1.0 - (x - 0.5) ** 2, "n_trades": 100}

    opt_dir = _run(tmp_path, monkeypatch, f, "opt-reselect")
    a = reselect(opt_dir, timestamp="2026A")
    b = reselect(opt_dir, timestamp="2026B")
    # Strip the timestamp from filenames; compare payload byte-equal.
    assert a.read_text() == b.read_text()


def test_reselect_threshold_flip_changes_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def f(p: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        bucket = min(3, max(0, int(x * 4)))
        start = s["start"]
        fold = 0
        for i, year in enumerate(("2020", "2021", "2022", "2023")):
            if year in start:
                fold = i
                break
        return {"sharpe": 10.0 if bucket == fold else -1.0, "n_trades": 50}

    opt_dir = _run(tmp_path, monkeypatch, f, "opt-overfit")
    rejected = json.loads((opt_dir / "best.json").read_text())
    assert rejected["decision"]["status"] == "rejected_pbo"
    out = reselect(opt_dir, pbo_threshold=1.0, timestamp="flipped")
    flipped = json.loads(out.read_text())
    assert flipped["decision"]["status"] == "accepted"
    assert flipped["final"] is not None
    # Manifest records the reselection event.
    manifest = json.loads((opt_dir / "manifest.json").read_text())
    history = manifest.get("reselection_history") or []
    assert any(h.get("timestamp") == "flipped" for h in history)


def test_force_at_run_records_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def f(p: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        bucket = min(3, max(0, int(x * 4)))
        start = s["start"]
        fold = 0
        for i, year in enumerate(("2020", "2021", "2022", "2023")):
            if year in start:
                fold = i
                break
        return {"sharpe": 10.0 if bucket == fold else -1.0, "n_trades": 50}

    experiment = espec.load(_spec(tmp_path))
    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)
    eng = _StubEngine(f)
    runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="opt-forced",
        persist_writer=writer,
        selection_overrides=SelectionOverrides(force=True),
    )
    opt_dir = ledger_root / "optimizations" / "opt-forced"
    best = json.loads((opt_dir / "best.json").read_text())
    assert best["decision"]["status"] == "accepted"
    assert best["decision"]["force_override"] is True
    # PBO is computed and persisted even with --force.
    assert best["pbo"]["value"] > 0.5


def test_robust_objective_reselect_runs_and_records_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``optimize reselect --robust-objective`` ranks via robust score.

    The selector unit test pins the case where the robust-score and DSR
    rankings actually disagree; this test exercises the post-hoc reselect
    integration with ``robust_objective=True`` end-to-end: it must produce
    a valid artifact and record the robust-objective flag in the decision.
    """

    def f(p: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        start = s["start"]
        if x < 0.5:
            high = "2020" in start or "2022" in start
            return {"sharpe": 10.0 if high else 0.0, "n_trades": 100}
        return {"sharpe": 4.5, "n_trades": 100}

    experiment = espec.load(_spec(tmp_path))
    monkeypatch.setattr(runner, "evaluate_spec", _accept_all)
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)
    eng = _StubEngine(f)
    runner.run_optimization(
        experiment=experiment,
        objective={"primary": {"metric": "sharpe"}},
        engine=eng,  # type: ignore[arg-type]
        artifact_path=experiment.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="opt-knife",
        persist_writer=writer,
    )
    opt_dir = ledger_root / "optimizations" / "opt-knife"
    out = reselect(opt_dir, robust_objective=True, timestamp="robust")
    robust_payload = json.loads(out.read_text())
    assert robust_payload["decision"]["status"] in ("accepted", "rejected_pbo")
    assert robust_payload["decision"]["robust_objective"] is True
    # robust_score is always reported for every top-K candidate
    assert robust_payload["sensitivity_score"]
    for entry in robust_payload["sensitivity_score"]:
        assert "robust_score" in entry
        assert "neighborhood_mean" in entry
        assert "neighborhood_std" in entry
