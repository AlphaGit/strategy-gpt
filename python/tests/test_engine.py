"""Integration tests for the `Engine` wrapper over the PyO3 engine bindings.

These tests build the `example-strategy` cdylib via `cargo build` (so they
are self-contained) and exercise `submit_batch` / `poll` / `cancel` on a
small synthetic dataset.

Skipped when the native extension has not been built (`maturin develop`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strategy_gpt._native_shim import native_available
from strategy_gpt.engine import Engine
from strategy_gpt.types import Bar, Resolution

pytestmark = pytest.mark.skipif(
    not native_available(), reason="native extension not built (run `maturin develop`)"
)


def _build_example_strategy() -> Path:
    """Build the example-strategy cdylib and return its absolute path."""
    repo_root = Path(__file__).resolve().parents[2]
    crates_root = repo_root / "crates"
    subprocess.run(
        ["cargo", "build", "-p", "example-strategy"],  # noqa: S607 — cargo on $PATH by setup.
        cwd=crates_root,
        check=True,
    )
    if sys.platform == "darwin":
        name = "libexample_strategy.dylib"
    elif sys.platform == "win32":
        name = "example_strategy.dll"
    else:
        name = "libexample_strategy.so"
    artifact = crates_root / "target" / "debug" / name
    assert artifact.exists(), f"missing artifact {artifact}"
    return artifact


def _synthetic_bars(n: int = 30) -> list[Bar]:
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


def _spec_for(bars: list[Bar]) -> dict[str, object]:
    start_iso = bars[0].ts.isoformat()
    end_iso = (bars[-1].ts + timedelta(days=1)).isoformat()
    return {
        "strategy": "example_noop_artifact",
        "dataset": "manifest_hash",
        "runs": [
            {
                "params": {},
                "modes": [{"kind": "plain"}],
                "seed": 1,
                "slice": {
                    "start": start_iso,
                    "end": end_iso,
                },
            }
        ],
        "engine": {
            "fill_model": "NextBarOpen",
            "initial_capital": 100_000.0,
            "commission_per_fill": 0.0,
            "slippage_bps": 0.0,
            "sanity": {
                "max_intent_size": 1.0e9,
                "max_position_size": 1.0e9,
            },
        },
        "parallelism": 1,
    }


def _wait_for_terminal(engine, handle: str, timeout_s: float = 10.0):
    """Poll until the job leaves the running state or `timeout_s` expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = engine.poll(handle)
        if status.status != "running":
            return status
        time.sleep(0.05)
    pytest.fail("engine job did not terminate within timeout")


def test_submit_batch_completes_with_results() -> None:
    artifact = _build_example_strategy()
    bars = _synthetic_bars(30)
    spec = _spec_for(bars)

    engine = Engine()
    handle = engine.submit_batch(artifact, bars, spec, "manifest_hash")
    status = _wait_for_terminal(engine, handle)

    assert status.status == "completed", f"job failed: {status.error}"
    assert status.results is not None
    assert len(status.results) == 1
    result = status.results[0]
    assert result["meta"]["dataset_manifest"] == "manifest_hash"


def test_poll_unknown_handle_raises() -> None:
    engine = Engine()
    with pytest.raises(ValueError, match="unknown handle"):
        engine.poll("not-a-real-handle")


def test_drop_handle_releases_state() -> None:
    artifact = _build_example_strategy()
    bars = _synthetic_bars(10)
    spec = _spec_for(bars)

    engine = Engine()
    handle = engine.submit_batch(artifact, bars, spec, "manifest_hash")
    _wait_for_terminal(engine, handle)

    assert engine.drop_handle(handle) is True
    assert engine.drop_handle(handle) is False


def test_cancel_unknown_handle_raises() -> None:
    engine = Engine()
    with pytest.raises(ValueError, match="unknown handle"):
        engine.cancel("not-a-real-handle")


def test_poll_payload_is_valid_json() -> None:
    """JobStatus is parsed via pydantic; confirm the raw payload shape is sane."""
    artifact = _build_example_strategy()
    bars = _synthetic_bars(5)
    spec = _spec_for(bars)
    engine = Engine()
    handle = engine.submit_batch(artifact, bars, spec, "manifest_hash")
    status = _wait_for_terminal(engine, handle)
    # Round-trip through model_dump_json to confirm consistency.
    json.loads(status.model_dump_json())
