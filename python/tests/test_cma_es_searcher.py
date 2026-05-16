"""Unit tests for the CMA-ES searcher."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.benchmark import planned_run_count
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimizer import (
    cma_dedup_rate,
    cma_resolve_popsize,
    cma_unit_to_params,
)
from strategy_gpt.types import EvaluationOutcome


def test_cma_resolve_popsize_auto() -> None:
    """Hansen 2016 default: 4 + floor(3 * ln(D))."""
    assert cma_resolve_popsize("auto", 1) == 4
    assert cma_resolve_popsize("auto", 2) == 4 + int(3 * math.log(2))
    assert cma_resolve_popsize("auto", 10) == 4 + int(3 * math.log(10))
    assert cma_resolve_popsize(12, 5) == 12
    assert cma_resolve_popsize(1, 5) == 4


def test_cma_unit_to_params_clip_and_int() -> None:
    keys = ["x", "n"]
    bounds = [(0.0, 1.0), (0.0, 10.0)]
    integrality = [False, True]
    # Inside bounds.
    out = cma_unit_to_params([0.25, 0.5], keys, bounds, integrality, "clip")
    assert out["x"] == pytest.approx(0.25)
    assert out["n"] == 5
    # Clip out-of-bounds.
    out = cma_unit_to_params([-0.5, 1.5], keys, bounds, integrality, "clip")
    assert out["x"] == 0.0
    assert out["n"] == 10


def test_cma_dedup_rate() -> None:
    assert cma_dedup_rate([]) == 0.0
    assert cma_dedup_rate([{"x": 1}, {"x": 2}, {"x": 3}]) == 0.0
    assert cma_dedup_rate([{"x": 1}, {"x": 1}, {"x": 1}]) == pytest.approx(2 / 3)


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


def _spec_cma(tmp_path: Path, *, n_gen: int = 12, popsize: int | str = "auto") -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    pop_yaml = popsize if isinstance(popsize, str) else str(popsize)
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
    x: {{ type: float, low: -2.0, high: 2.0 }}
    y: {{ type: float, low: -2.0, high: 2.0 }}
  cma_es:
    popsize: {pop_yaml}
    sigma0: 0.3
    n_generations: {n_gen}
  persist: {{ root: ./ledger, name: cma-smoke }}
parallelism: 1
""")
    return p


def test_cma_end_to_end_converges_on_paraboloid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -((x - 0.6) ** 2 + (y + 0.4) ** 2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_cma(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="cma-test",
    )
    assert r.final is not None
    assert abs(r.final.params["x"] - 0.6) < 0.3
    assert abs(r.final.params["y"] - -0.4) < 0.3


def test_cma_determinism_seed_reproduces_trial_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        return {"sharpe": -(float(p["x"]) ** 2 + float(p["y"]) ** 2), "n_trades": 10}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_cma(tmp_path, n_gen=6))
    r1 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="cma-det-1",
    )
    r2 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="cma-det-2",
    )
    train1 = [t.params for t in r1.trial_rows if t.phase.startswith("train_fold_")]
    train2 = [t.params for t in r2.trial_rows if t.phase.startswith("train_fold_")]
    assert train1 == train2


def test_cma_integer_params_stay_integer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed (2 float, 2 int) space; int dims stay integer everywhere."""
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
  seed: 13
  aggregator: mean
  space:
    a: {{ type: float, low: -1.0, high: 1.0 }}
    b: {{ type: float, low: -1.0, high: 1.0 }}
    n: {{ type: int, low: 2, high: 20 }}
    m: {{ type: int, low: 0, high: 10 }}
  cma_es:
    popsize: 12
    sigma0: 0.3
    n_generations: 5
  persist: {{ root: ./ledger, name: cma-mixed }}
parallelism: 1
""")

    def metric_fn(params: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        a = float(params["a"])
        b = float(params["b"])
        n = int(params["n"])
        m = int(params["m"])
        return {
            "sharpe": -(a**2 + b**2) - 0.01 * ((n - 10) ** 2 + (m - 5) ** 2),
            "n_trades": 50,
        }

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(p)
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="cma-mixed",
    )
    int_seen = 0
    for row in r.trial_rows:
        if "n" in row.params:
            assert isinstance(row.params["n"], int)
            assert isinstance(row.params["m"], int)
            int_seen += 1
    assert int_seen > 0


def test_cma_predictor_plan_counts(tmp_path: Path) -> None:
    """``popsize * n_generations * folds + folds**2``."""
    exp = espec.load(_spec_cma(tmp_path, n_gen=20, popsize=10))
    assert exp.optimize is not None
    expected = 10 * 20 * 2 + 2 * 2
    assert planned_run_count(exp.optimize, folds_count=2) == expected


def test_cma_rejects_unsupported_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
  seed: 1
  aggregator: mean
  space:
    x: {{ type: float, low: -1.0, high: 1.0 }}
  cma_es:
    restart_strategy: ipop
    n_generations: 2
  persist: {{ root: ./ledger, name: cma-restart }}
parallelism: 1
""")

    def metric_fn(_p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        return {"sharpe": 0.0, "n_trades": 0}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(p)
    with pytest.raises(NotImplementedError, match="restart_strategy"):
        runner.run_optimization(
            experiment=exp,
            objective={"primary": {"metric": "sharpe"}},
            engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
            artifact_path=exp.artifact,
            bars=[],
            dataset_manifest="deadbeef",
            opt_id="cma-restart",
        )
