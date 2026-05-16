"""Unit + integration tests for the LHS + Hooke-Jeeves searcher."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.benchmark import planned_run_count
from strategy_gpt.engine import JobStatus
from strategy_gpt.optimizer import ContinuousParam, LhsSearcher, hooke_jeeves_propose
from strategy_gpt.types import EvaluationOutcome


def test_hooke_jeeves_propose_shape() -> None:
    """Each dim contributes a ``+step`` and a ``-step`` probe, clamped to [0, 1]."""
    base = [0.5, 0.5, 0.5]
    step = [0.1, 0.2, 0.3]
    probes = hooke_jeeves_propose(base, step)
    assert len(probes) == 2 * 3
    # Probe order: plus_0, minus_0, plus_1, minus_1, plus_2, minus_2.
    assert probes[0] == [0.6, 0.5, 0.5]
    assert probes[1] == [0.4, 0.5, 0.5]
    assert probes[2] == [0.5, 0.7, 0.5]
    assert probes[3] == [0.5, 0.3, 0.5]


def test_hooke_jeeves_clamps_at_unit_cube_boundary() -> None:
    """Probes at the boundary clamp to [0, 1] without going negative."""
    probes = hooke_jeeves_propose([0.95, 0.05], [0.2, 0.2])
    assert probes[0] == [1.0, 0.05]  # plus clamped to 1.0
    assert probes[1] == [0.75, 0.05]
    assert probes[2] == [0.95, 0.25]
    assert probes[3] == [0.95, 0.0]  # minus clamped to 0.0


def test_lhs_searcher_yields_n_points() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0), "y": ContinuousParam(low=-1.0, high=1.0)}
    out = list(LhsSearcher(space=space, n_points=32, seed=42).candidates())
    assert len(out) == 32
    xs = [p["x"] for p in out]
    ys = [p["y"] for p in out]
    assert min(xs) < 0.2
    assert max(xs) > 0.8
    assert min(ys) < -0.6
    assert max(ys) > 0.6


def test_lhs_searcher_deterministic() -> None:
    space = {"x": ContinuousParam(low=0.0, high=1.0)}
    a = list(LhsSearcher(space=space, n_points=16, seed=7).candidates())
    b = list(LhsSearcher(space=space, n_points=16, seed=7).candidates())
    assert a == b
    c = list(LhsSearcher(space=space, n_points=16, seed=8).candidates())
    assert a != c


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


def _spec_lhs(tmp_path: Path) -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: lhs
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 2, scheme: rolling }}
optimize:
  method: lhs_polish
  seed: 11
  aggregator: mean
  space:
    x: {{ type: float, low: -2.0, high: 2.0 }}
    y: {{ type: float, low: -2.0, high: 2.0 }}
  lhs_polish:
    lhs_n: 32
    top_k_polish: 2
    polish: hooke_jeeves
    initial_step: 0.1
    step_min: 0.005
    max_polish_iters: 30
    lhs_seed: 11
  persist: {{ root: ./ledger, name: lhs-smoke }}
parallelism: 1
""")
    return p


def test_lhs_polish_converges_on_paraboloid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -((x - 0.7) ** 2 + (y + 0.5) ** 2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_lhs(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="lhs-test",
    )
    assert r.final is not None
    # Polish should pull the LHS top-K closer to the (0.7, -0.5) optimum.
    assert abs(r.final.params["x"] - 0.7) < 0.5
    assert abs(r.final.params["y"] - -0.5) < 0.5


def test_hooke_jeeves_halves_step_on_plateau(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no probe improves, step size halves; eventually all dims hit step_min."""
    # Constant objective -> every probe ties the best; Hooke-Jeeves should
    # halve the step each iter until step_min triggers termination. Verify
    # the polish phase emits fewer than max_iters * 2D rows because
    # trajectories deactivate.

    def metric_fn(_p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        return {"sharpe": 1.0, "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_lhs(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="lhs-plateau",
    )
    polish_rows = [t for t in r.trial_rows if t.phase.startswith("train_fold_") and t.round > 0]
    # initial_step=0.1, step_min=0.005 -> 5 halvings reach step_min (0.1/2^5=0.003).
    # Max polish trial count per fold = top_k_polish * 2D * halving_steps = 2 * 4 * ~5.
    # Times 2 folds, well below max_polish_iters (30) * top_k * 2D = 480.
    assert len(polish_rows) < 30 * 2 * 4 * 2


def test_lhs_polish_planned_run_count_includes_polish(tmp_path: Path) -> None:
    """``(lhs_n + top_k * 2 * D * max_iters) * folds + folds**2``."""
    exp = espec.load(_spec_lhs(tmp_path))
    assert exp.optimize is not None
    lhs_n = 32
    top_k = 2
    d = 2
    max_iters = 30
    folds = 2
    expected = (lhs_n + top_k * 2 * d * max_iters) * folds + folds * folds
    assert planned_run_count(exp.optimize, folds_count=folds) == expected


def test_nelder_mead_polish_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: lhs
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 2, scheme: rolling }}
optimize:
  method: lhs_polish
  seed: 1
  aggregator: mean
  space:
    x: {{ type: float, low: 0.0, high: 1.0 }}
  lhs_polish:
    lhs_n: 8
    top_k_polish: 1
    polish: nelder_mead
    max_polish_iters: 5
  persist: {{ root: ./ledger, name: lhs-nm }}
parallelism: 1
""")

    def metric_fn(_p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        return {"sharpe": 0.0, "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(p)
    with pytest.raises(NotImplementedError, match="nelder_mead"):
        runner.run_optimization(
            experiment=exp,
            objective={"primary": {"metric": "sharpe"}},
            engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
            artifact_path=exp.artifact,
            bars=[],
            dataset_manifest="deadbeef",
            opt_id="lhs-nm",
        )
