"""Byte-identity: ledger output unaffected by `--progress` mode.

The spec mandates progress is a UX channel only; identical inputs must
produce byte-identical ledger rows whether --progress is on or off.

This test exercises that by running the engine twice with the same
inputs — once with a no-op progress bus, once without any bus — and
comparing the resulting BacktestResult shapes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strategy_gpt._native_shim import native_available
from strategy_gpt.engine import Engine
from strategy_gpt.progress import ProgressBus
from strategy_gpt.progress.sinks.base import NullSink
from strategy_gpt.types import Bar, FailureMode, Resolution

pytestmark = pytest.mark.skipif(
    not native_available(), reason="native extension not built (run `maturin develop`)"
)


def _build_artifacts() -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[2]
    crates_root = repo_root / "crates"
    subprocess.run(
        ["cargo", "build", "-p", "example-strategy"],  # noqa: S607 — cargo on $PATH by setup
        cwd=crates_root,
        check=True,
    )
    subprocess.run(
        ["cargo", "build", "-p", "engine", "--bin", "engine-worker"],  # noqa: S607
        cwd=crates_root,
        check=True,
    )
    if sys.platform == "darwin":
        lib = "libexample_strategy.dylib"
    elif sys.platform == "win32":
        lib = "example_strategy.dll"
    else:
        lib = "libexample_strategy.so"
    worker = "engine-worker.exe" if sys.platform == "win32" else "engine-worker"
    target = crates_root / "target" / "debug"
    return target / lib, target / worker


def _toy_bars() -> list[Bar]:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    return [
        Bar(
            ts=start + timedelta(days=i),
            symbol="TEST",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
            resolution=Resolution.DAY,
        )
        for i in range(20)
    ]


def _batch_spec(artifact: Path) -> dict[str, object]:
    return {
        "strategy": str(artifact),
        "dataset": "toy_manifest",
        "runs": [
            {
                "params": {},
                "modes": [{"kind": "plain"}],
                "slice": {
                    "start": "2024-01-02T00:00:00Z",
                    "end": "2024-01-22T00:00:00Z",
                },
                "seed": 0,
            }
        ],
        "engine": {
            "fill_model": "NextBarOpen",
            "initial_capital": 100000.0,
            "slippage_bps": 0.0,
            "commission_per_fill": 0.0,
            "sanity": {
                "max_intent_size": 1.0e9,
                "max_position_size": 1.0e9,
            },
        },
        "parallelism": 1,
        "failure_mode": FailureMode.ABORT.value,
    }


def _run_once(*, with_bus: bool) -> str:
    artifact, worker = _build_artifacts()
    eng = Engine(worker)
    if with_bus:
        bus = ProgressBus(sinks=[NullSink()])
        eng.attach_progress_bridge(bus)
    bars = _toy_bars()
    spec = _batch_spec(artifact)
    handle = eng.submit_batch(str(artifact), bars, spec, "toy_manifest")
    deadline = time.monotonic() + 30
    while True:
        status = eng.poll(handle)
        if status.status == "completed":
            assert status.results is not None
            payload = json.dumps(status.results, sort_keys=True)
            return hashlib.sha256(payload.encode()).hexdigest()
        if status.status in ("failed", "cancelled"):
            raise RuntimeError(f"engine: {status.status} — {status.error!r}")
        if time.monotonic() > deadline:
            raise TimeoutError
        time.sleep(0.05)


def test_progress_off_and_on_yield_identical_results() -> None:
    first = _run_once(with_bus=False)
    second = _run_once(with_bus=True)
    assert first == second, "progress bus must not alter the BacktestResult byte-identity"
