"""Unit tests for the DE helpers + end-to-end DE fold search."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.benchmark import planned_run_count
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimizer import (
    ChoiceParam,
    ContinuousParam,
    IntParam,
    SobolSearcher,
    de_bounds_and_integrality,
    de_project_individual,
    de_resolve_popsize,
    de_sobol_init,
)
from strategy_gpt.types import EvaluationOutcome


def test_de_bounds_and_integrality_strips_categoricals() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0), "n": IntParam(low=2, high=10)}
    keys, bounds, integrality = de_bounds_and_integrality(space)
    assert keys == ["x", "n"]
    assert bounds == [(0.0, 1.0), (2.0, 10.0)]
    assert integrality == [False, True]


def test_de_bounds_rejects_choice() -> None:
    space = {"mode": ChoiceParam(choices=["a", "b"])}
    with pytest.raises(TypeError, match="ChoiceParam"):
        de_bounds_and_integrality(space)


def test_de_project_individual_int_param() -> None:
    keys = ["x", "n"]
    integrality = [False, True]
    out = de_project_individual([0.5, 3.7], keys, integrality)
    assert out["x"] == 0.5
    assert isinstance(out["n"], int)
    assert out["n"] == 4


def test_de_resolve_popsize() -> None:
    assert de_resolve_popsize("auto", 2) == 30
    assert de_resolve_popsize("auto", 5) == 75
    assert de_resolve_popsize(20, 2) == 20
    assert de_resolve_popsize(2, 5) == 5


def test_de_sobol_init_matches_standalone_sobol() -> None:
    """Task 10.4: first DE generation matches standalone Sobol with same seed/popsize."""
    space = {"x": ContinuousParam(low=0.0, high=1.0), "y": ContinuousParam(low=-1.0, high=1.0)}
    keys = ["x", "y"]
    popsize = 16
    init = de_sobol_init(space, keys, popsize, seed=42)
    assert init.shape == (popsize, 2)
    standalone = list(
        SobolSearcher(space=space, n_points=popsize, scramble=True, owen_seed=42).candidates()
    )
    expected = np.array([[p["x"], p["y"]] for p in standalone[:popsize]])
    assert np.allclose(init, expected)


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


def _spec_de(tmp_path: Path) -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: de
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 4, scheme: rolling }}
optimize:
  method: differential_evolution
  seed: 7
  aggregator: mean
  space:
    x: {{ type: float, low: -2.0, high: 2.0 }}
    y: {{ type: float, low: -2.0, high: 2.0 }}
  differential_evolution:
    popsize: 12
    n_generations: 8
    strategy: best1bin
    crossover: 0.7
    init: sobol
    tol: 0.0
  persist: {{ root: ./ledger, name: de-smoke }}
parallelism: 1
""")
    return p


def test_de_end_to_end_converges_on_paraboloid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DE converges on a 2-D paraboloid centered at (0.5, -0.3)."""

    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        sharpe = -((x - 0.5) ** 2 + (y + 0.3) ** 2)
        return {"sharpe": sharpe, "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_de(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="de-test",
    )
    assert r.final is not None
    assert abs(r.final.params["x"] - 0.5) < 0.2
    assert abs(r.final.params["y"] - -0.3) < 0.2


def test_de_determinism_seed_reproduces_trial_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same seed + knobs → identical training-trial param sequence."""

    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -(x**2 + y**2), "n_trades": 10}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_de(tmp_path))
    r1 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="de-det-1",
    )
    r2 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="de-det-2",
    )
    train1 = [t.params for t in r1.trial_rows if t.phase.startswith("train_fold_")]
    train2 = [t.params for t in r2.trial_rows if t.phase.startswith("train_fold_")]
    assert train1 == train2


def test_de_integer_params_stay_integer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integer search dimension yields only integer values in the trial log."""
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: de
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 2, scheme: rolling }}
optimize:
  method: differential_evolution
  seed: 3
  aggregator: mean
  space:
    x: {{ type: float, low: 0.0, high: 1.0 }}
    n: {{ type: int, low: 2, high: 20 }}
  differential_evolution:
    popsize: 10
    n_generations: 4
    init: sobol
  persist: {{ root: ./ledger, name: de-int }}
parallelism: 1
""")

    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        n = int(p["n"])
        return {"sharpe": -((x - 0.5) ** 2) - ((n - 10) ** 2) * 0.01, "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(p)
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="de-int",
    )
    for row in r.trial_rows:
        if "n" in row.params:
            assert isinstance(row.params["n"], int)
            assert 2 <= row.params["n"] <= 20


def test_de_predictor_plan_counts(tmp_path: Path) -> None:
    """Benchmark predictor uses ``popsize * n_generations * folds + folds**2``."""
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    spec_path = tmp_path / "exp.yaml"
    spec_path.write_text(f"""
artifact: {artifact}
strategy_label: de
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 4, scheme: rolling }}
optimize:
  method: differential_evolution
  seed: 3
  aggregator: mean
  space:
    x: {{ type: float, low: 0.0, high: 1.0 }}
    y: {{ type: float, low: 0.0, high: 1.0 }}
  differential_evolution:
    popsize: 20
    n_generations: 10
  persist: {{ root: ./ledger, name: de-pred }}
parallelism: 1
""")
    exp = espec.load(spec_path)
    assert exp.optimize is not None
    expected = 20 * 10 * 4 + 4 * 4
    assert planned_run_count(exp.optimize, folds_count=4) == expected
