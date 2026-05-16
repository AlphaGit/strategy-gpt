"""End-to-end Tester integration tests.

These tests exercise the full pipeline against the in-tree
``example-strategy`` fixture: build pipeline → engine submission → smoke
classification → verdict. Skipped when the native extension has not been
built (``maturin develop``). The compilation steps are slow on first
invocation but cached on subsequent runs.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strategy_gpt._native_shim import native_available
from strategy_gpt.build_pipeline import (
    BuildPipeline,
    ManifestDep,
    StrategyManifest,
)
from strategy_gpt.engine import Engine
from strategy_gpt.hypothesis_loop import HypothesisCandidate
from strategy_gpt.tester import (
    SmokePolicy,
    Verdict,
    VerdictKind,
    evaluate_verdict,
    run_smoke,
)
from strategy_gpt.types import Bar, Resolution

pytestmark = pytest.mark.skipif(
    not native_available(), reason="native extension not built (run `maturin develop`)"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _crates_root() -> Path:
    return _repo_root() / "crates"


def _build_example_strategy() -> Path:
    subprocess.run(
        ["cargo", "build", "-p", "example-strategy"],  # noqa: S607 — cargo on $PATH
        cwd=_crates_root(),
        check=True,
    )
    if sys.platform == "darwin":
        name = "libexample_strategy.dylib"
    elif sys.platform == "win32":
        name = "example_strategy.dll"
    else:
        name = "libexample_strategy.so"
    artifact = _crates_root() / "target" / "debug" / name
    assert artifact.exists()
    return artifact


def _build_engine_worker() -> Path:
    subprocess.run(
        ["cargo", "build", "-p", "engine", "--bin", "engine-worker"],  # noqa: S607
        cwd=_crates_root(),
        check=True,
    )
    name = "engine-worker.exe" if sys.platform == "win32" else "engine-worker"
    binary = _crates_root() / "target" / "debug" / name
    assert binary.exists()
    return binary


def _bars(n: int = 10) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="VXX",
            ts=start + timedelta(days=i),
            resolution=Resolution.DAY,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1000.0,
        )
        for i in range(n)
    ]


def test_run_smoke_against_example_strategy_succeeds() -> None:
    """End-to-end smoke run on the fixture cdylib. The example strategy is
    a noop so we use ``min_trades=0`` — the test asserts the orchestration
    plumbing (engine submit + poll + drop) works, not strategy behaviour.
    """
    artifact = _build_example_strategy()
    worker = _build_engine_worker()
    engine = Engine(worker)
    bars = _bars(15)
    outcome = run_smoke(
        engine,
        strategy_artifact=str(artifact),
        dataset_ref="fixture-2024",
        bars=bars,
        params={},
        slice_start=bars[0].ts,
        slice_end=bars[-1].ts + timedelta(days=1),
        dataset_manifest="fixture-2024",
        policy=SmokePolicy(min_trades=0, poll_interval_secs=0.05, timeout_secs=30.0),
    )
    assert outcome.ok is True, outcome
    assert outcome.metrics is not None


def test_full_pipeline_build_smoke_verdict() -> None:
    """End-to-end: compile a minimal strategy via the build pipeline,
    smoke it, and evaluate the falsification criterion against the
    returned metrics.

    Uses the same minimal source the build-pipeline integration test in
    `crates/build-pipeline` exercises so the fixture cost amortizes
    across both crates' caches.
    """
    cache_root = _repo_root() / "target" / "tester-build-cache"
    work_root = _repo_root() / "target" / "tester-build-work"
    cache_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    pipeline = BuildPipeline(
        cache_root=cache_root,
        work_root=work_root,
        engine_rt_path=_crates_root() / "engine-rt",
        whitelist_path=_crates_root() / "build-pipeline" / "whitelist.toml",
        profile="release",
    )
    source = """
        use engine_rt::{strategy_entry, Bar, Context, Result, Sealed, Strategy, StrategyMeta};

        #[derive(Default)]
        pub struct M;
        impl Sealed for M {}
        impl Strategy for M {
            fn metadata(&self) -> StrategyMeta {
                StrategyMeta::new("m", "0.1.0", "tester", "minimal")
            }
            fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> Result<()> { Ok(()) }
        }
        fn make() -> Box<dyn Strategy> { Box::<M>::default() }
        strategy_entry!(make);
    """
    manifest = StrategyManifest(
        name="tester_minimal",
        version="0.1.0",
        dependencies=[ManifestDep(name="engine-rt", req="*")],
    )
    outcome = pipeline.build(source, manifest)
    library = Path(outcome.artifact.library_path)
    assert library.exists()

    worker = _build_engine_worker()
    engine = Engine(worker)
    bars = _bars(15)
    smoke = run_smoke(
        engine,
        strategy_artifact=str(library),
        dataset_ref="fixture-2024",
        bars=bars,
        params={},
        slice_start=bars[0].ts,
        slice_end=bars[-1].ts + timedelta(days=1),
        dataset_manifest="fixture-2024",
        policy=SmokePolicy(min_trades=0, poll_interval_secs=0.05, timeout_secs=60.0),
    )
    assert smoke.ok is True, smoke
    assert smoke.metrics is not None

    candidate = HypothesisCandidate(
        name="noop_baseline",
        target_metric="n_trades",
        falsification={"op": ">=", "threshold": 0},
        proposed_change={"param": "noop", "to": True},
        estimated_lift_confidence=0.1,
    )
    verdict: Verdict = evaluate_verdict(candidate, smoke.metrics)
    assert verdict.kind is VerdictKind.PASSED
    assert verdict.criterion.metric == "n_trades"
