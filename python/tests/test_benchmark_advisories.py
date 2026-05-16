"""Tests for the method/space advisories surfaced in the benchmark report."""

from __future__ import annotations

from pathlib import Path

from strategy_gpt import experiment_spec as espec
from strategy_gpt.benchmark import _method_advisories


def _spec(tmp_path: Path, body: str) -> Path:
    artifact = tmp_path / "x.dylib"
    artifact.write_bytes(b"")
    p = tmp_path / "exp.yaml"
    p.write_text(f"""
artifact: {artifact}
strategy_label: stub
bars: {{ dataset: deadbeef }}
runs:
  - params: {{}}
    modes: [{{ kind: plain }}]
    seed: 1
    slice: {{ start: 2020-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z }}
folds: {{ count: 2, scheme: rolling }}
{body}
parallelism: 1
""")
    return p


def test_cma_advisory_fires_when_int_dims_dominate(tmp_path: Path) -> None:
    spec_body = """
optimize:
  method: cma_es
  seed: 1
  aggregator: mean
  space:
    x: { type: float, low: 0.0, high: 1.0 }
    n: { type: int, low: 0, high: 10 }
    m: { type: int, low: 0, high: 10 }
  cma_es: { popsize: 8, n_generations: 5 }
  persist: { root: ./ledger, name: cma-advisory }
"""
    exp = espec.load(_spec(tmp_path, spec_body))
    assert exp.optimize is not None
    advs = _method_advisories(exp.optimize)
    assert any("differential_evolution" in a for a in advs)


def test_cma_advisory_silent_when_floats_dominate(tmp_path: Path) -> None:
    spec_body = """
optimize:
  method: cma_es
  seed: 1
  aggregator: mean
  space:
    x: { type: float, low: 0.0, high: 1.0 }
    y: { type: float, low: 0.0, high: 1.0 }
    n: { type: int, low: 0, high: 10 }
  cma_es: { popsize: 8, n_generations: 5 }
  persist: { root: ./ledger, name: cma-quiet }
"""
    exp = espec.load(_spec(tmp_path, spec_body))
    assert exp.optimize is not None
    advs = _method_advisories(exp.optimize)
    assert not any("differential_evolution" in a for a in advs)


def test_sobol_sparse_coverage_advisory(tmp_path: Path) -> None:
    """n_points=16 < 2^(2+8)=1024 -> coverage advisory fires for 2D."""
    spec_body = """
optimize:
  method: sobol
  seed: 1
  aggregator: mean
  space:
    x: { type: float, low: 0.0, high: 1.0 }
    y: { type: float, low: 0.0, high: 1.0 }
  sobol: { n_points: 16, scramble: true, owen_seed: 0 }
  persist: { root: ./ledger, name: sobol-sparse }
"""
    exp = espec.load(_spec(tmp_path, spec_body))
    assert exp.optimize is not None
    advs = _method_advisories(exp.optimize)
    assert any("coverage may be too sparse" in a for a in advs)


def test_sobol_dense_coverage_silent(tmp_path: Path) -> None:
    """n_points=2048 > 2^(2+8)=1024 -> no advisory."""
    spec_body = """
optimize:
  method: sobol
  seed: 1
  aggregator: mean
  space:
    x: { type: float, low: 0.0, high: 1.0 }
    y: { type: float, low: 0.0, high: 1.0 }
  sobol: { n_points: 2048, scramble: true, owen_seed: 0 }
  persist: { root: ./ledger, name: sobol-dense }
"""
    exp = espec.load(_spec(tmp_path, spec_body))
    assert exp.optimize is not None
    advs = _method_advisories(exp.optimize)
    assert not any("coverage" in a for a in advs)


def test_non_cma_non_sobol_methods_silent(tmp_path: Path) -> None:
    spec_body = """
optimize:
  method: grid
  seed: 1
  aggregator: mean
  space:
    n: { type: int, low: 0, high: 10 }
    m: { type: int, low: 0, high: 10 }
  grid: { resolution: 5 }
  persist: { root: ./ledger, name: grid-quiet }
"""
    exp = espec.load(_spec(tmp_path, spec_body))
    assert exp.optimize is not None
    assert _method_advisories(exp.optimize) == []
