"""Determinism manifest records library versions + resolved auto knobs."""

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
from strategy_gpt.optimization_ledger import OptimizationLedger
from strategy_gpt.types import EvaluationOutcome


class _StubEngine:
    def __init__(self, metric_fn: Any) -> None:
        self.metric_fn = metric_fn
        self._jobs: dict[str, JobStatus] = {}
        self._counter = 0

    def submit_batch(self, *a: Any, run_id: str | None = None, **_kw: Any) -> str:
        spec = a[2]
        self._counter += 1
        h = f"j{self._counter}"
        self._jobs[h] = JobStatus(
            status="completed",
            results=[
                {
                    "status": "ok",
                    "run_index": i,
                    "result": {"metrics": self.metric_fn(r["params"], r["slice"])},
                }
                for i, r in enumerate(spec["runs"])
            ],
        )
        return h

    def poll(self, h: str) -> JobStatus:
        return self._jobs[h]

    def drop_handle(self, h: str) -> bool:
        return self._jobs.pop(h, None) is not None


def _accept(_o: Mapping[str, Any], m: Mapping[str, Any]) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=True,
        score=float(m.get("sharpe", float("-inf"))),
        violations=[],
        soft_misses=[],
    )


def _spec_cma(tmp_path: Path) -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: cma
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 2, scheme: rolling }}
optimize:
  method: cma_es
  seed: 11
  aggregator: mean
  space:
    x: {{ type: float, low: -1.0, high: 1.0 }}
    y: {{ type: float, low: -1.0, high: 1.0 }}
    z: {{ type: float, low: -1.0, high: 1.0 }}
  cma_es: {{ popsize: auto, sigma0: 0.3, n_generations: 3 }}
  persist: {{ root: ./ledger, name: manifest-cma }}
parallelism: 1
""")
    return p


def test_manifest_records_library_versions_and_resolved_knobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        return {
            "sharpe": -(float(p["x"]) ** 2 + float(p["y"]) ** 2 + float(p["z"]) ** 2),
            "n_trades": 10,
        }

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    ledger_root = tmp_path / "ledger"
    writer = OptimizationLedger(ledger_root)
    exp = espec.load(_spec_cma(tmp_path))
    runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="manifest-cma",
        persist_writer=writer,
    )
    manifest = json.loads(
        (ledger_root / "optimizations" / "manifest-cma" / "manifest.json").read_text()
    )
    libs = manifest["library_versions"]
    # Library versions snapshot — these methods rely on scipy + cma being pinned.
    assert "scipy" in libs
    assert "cma" in libs
    assert libs["scipy"] != "absent"
    assert libs["cma"] != "absent"
    # Resolved auto knobs: cma popsize = 4 + floor(3 * ln(3)) = 7.
    resolved = manifest["resolved_knobs"]
    assert resolved["method"] == "cma_es"
    assert resolved["n_dims"] == 3
    assert resolved["popsize"] == 7
    assert resolved["n_generations"] == 3
