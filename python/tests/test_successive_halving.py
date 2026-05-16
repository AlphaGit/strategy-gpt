"""Unit + integration tests for the Successive Halving searcher."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import experiment_spec as espec
from strategy_gpt import optimization_runner as runner
from strategy_gpt.benchmark import planned_run_count
from strategy_gpt.engine import JobStatus
from strategy_gpt.search.successive_halving import (
    _rung_budgets,
    _rung_survivor_counts,
)
from strategy_gpt.types import EvaluationOutcome


def test_rung_budgets_doubles_to_total() -> None:
    """initial_folds=2, eta=3, total=8 -> 2, 6, 8 (capped)."""
    assert _rung_budgets(2, 3, 8) == [2, 6, 8]
    assert _rung_budgets(2, 2, 8) == [2, 4, 8]
    assert _rung_budgets(1, 3, 9) == [1, 3, 9]
    assert _rung_budgets(2, 3, 2) == [2]
    assert _rung_budgets(2, 3, 5) == [2, 5]


def test_rung_survivor_counts_halving_cascade() -> None:
    """64 with eta=3 over 4 rungs -> 64, 22, 8, 3 (ceil(prev/eta))."""
    assert _rung_survivor_counts(64, 3, 4) == [64, 22, 8, 3]
    assert _rung_survivor_counts(64, 2, 4) == [64, 32, 16, 8]
    assert _rung_survivor_counts(8, 3, 3) == [8, 3, 1]


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


def _spec_sh(tmp_path: Path) -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: sh
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2028-01-01T00:00:00Z }}
folds: {{ count: 8, scheme: rolling }}
optimize:
  method: successive_halving
  seed: 7
  aggregator: mean
  space:
    x: {{ type: float, low: -2.0, high: 2.0 }}
    y: {{ type: float, low: -2.0, high: 2.0 }}
  successive_halving:
    initial_candidates: 16
    eta: 2
    initial_folds: 2
    init_method: sobol
    init_seed: 7
  persist: {{ root: ./ledger, name: sh-smoke }}
parallelism: 1
""")
    return p


def test_sh_end_to_end_converges_on_paraboloid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -((x - 0.4) ** 2 + (y + 0.3) ** 2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_sh(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="sh-test",
    )
    assert r.final is not None
    # Survivors should cluster near the optimum after the halving cascade.
    # With 16 Sobol candidates over a small budget, SH lands near the optimum
    # but not on it; allow 1.0 tolerance per dim (4x the dim half-range scaled).
    assert abs(r.final.params["x"] - 0.4) < 1.0
    assert abs(r.final.params["y"] - -0.3) < 1.0


def test_sh_killed_candidates_dont_appear_in_later_rungs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidates killed at rung r have parquet rows only for the rungs they ran on."""

    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -((x - 0.5) ** 2 + (y - 0.5) ** 2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_sh(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="sh-rungs",
    )
    train_rows = [t for t in r.trial_rows if t.phase.startswith("train_fold_")]
    # Phase format: train_fold_<i>_rung_<r>
    assert all("_rung_" in t.phase for t in train_rows)
    # Group candidate fingerprint -> set of rungs it appears in.
    by_params: dict[tuple, set[int]] = {}
    for t in train_rows:
        rung = int(t.phase.split("_rung_")[1])
        key = tuple(sorted(t.params.items()))
        by_params.setdefault(key, set()).add(rung)
    # The bottom-half-killed candidates have only rung 0; survivors span more.
    rung_spans = sorted([len(rungs) for rungs in by_params.values()])
    # With 16 candidates, eta=2 -> survivor counts 16,8,4,2 over rungs 0..3.
    # Some candidates ran 1 rung (killed at rung 0), others ran more.
    assert min(rung_spans) == 1
    assert max(rung_spans) >= 2


def test_sh_planned_run_count_sums_rung_costs(tmp_path: Path) -> None:
    """Predictor uses Σ_r (survivors_r * budget_r) + folds**2."""
    exp = espec.load(_spec_sh(tmp_path))
    assert exp.optimize is not None
    # 16 cands, eta=2, init_folds=2, total=8 folds.
    # Budgets: [2,4,8]; survivors: [16,8,4]; train = 16*2 + 8*4 + 4*8 = 96.
    expected_train = 16 * 2 + 8 * 4 + 4 * 8
    expected = expected_train + 8 * 8
    assert planned_run_count(exp.optimize, folds_count=8) == expected


def test_sh_final_winners_are_unique_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x = float(p["x"])
        return {"sharpe": -(x**2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_sh(tmp_path))
    r = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="sh-uniq",
    )
    fingerprints = {tuple(sorted(w.params.items())) for w in r.fold_winners}
    # SH's final-rung survivors are distinct candidates; each should map to
    # one FoldWinner.
    assert len(fingerprints) == len(r.fold_winners)


def test_sh_determinism_same_seed_same_winners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metric_fn(p: Mapping[str, Any], _s: Mapping[str, Any]) -> dict[str, float]:
        x, y = float(p["x"]), float(p["y"])
        return {"sharpe": -((x - 0.3) ** 2 + y**2), "n_trades": 50}

    monkeypatch.setattr(runner, "evaluate_spec", _accept)
    exp = espec.load(_spec_sh(tmp_path))
    r1 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="sh-d1",
    )
    r2 = runner.run_optimization(
        experiment=exp,
        objective={"primary": {"metric": "sharpe"}},
        engine=_StubEngine(metric_fn),  # type: ignore[arg-type]
        artifact_path=exp.artifact,
        bars=[],
        dataset_manifest="deadbeef",
        opt_id="sh-d2",
    )
    p1 = [tuple(sorted(t.params.items())) for t in r1.trial_rows if "_rung_" in t.phase]
    p2 = [tuple(sorted(t.params.items())) for t in r2.trial_rows if "_rung_" in t.phase]
    assert p1 == p2
