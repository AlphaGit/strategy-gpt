"""Parameter-optimization runner for the VXX reference strategy.

What this does
--------------
- Loads `crates/vxx-strategy/objective.yaml` (primary/secondary metrics,
  tradeoff mode, walk-forward config) and validates it through the Rust
  `objectives::validate_spec` binding.
- Enumerates a grid of `(vol_lo, vol_hi)` candidates over the
  realized-vol scale observed in the engine indicator.
- For each candidate, builds a `BatchSpec` against `examples/vxx/batch.json`
  with the candidate params, submits to the engine, polls until
  completion, extracts `metrics` from the single-run result, and scores
  them against the objective spec via `objectives::evaluate_spec`.
- Runs `strategy_gpt.optimizer.optimize(...)` and prints the best trial
  + a ranked summary.

The strategy logic is unchanged. Only the `params` JSON differs per
candidate; the artifact and dataset are reused across the whole sweep.

Run:
    python examples/vxx/optimize.py
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from strategy_gpt._native_shim import require_native
from strategy_gpt.engine import Engine
from strategy_gpt.objectives import validate_spec
from strategy_gpt.optimizer import GridSearcher, optimize
from strategy_gpt.types import EvaluationOutcome

REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "examples" / "vxx" / "batch.json"
BARS_PATH = REPO / "examples" / "vxx" / "bars.json"
OBJECTIVE_PATH = REPO / "crates" / "vxx-strategy" / "objective.yaml"
ARTIFACT = REPO / "crates" / "target" / "debug" / "libvxx_strategy.dylib"
WORKER = REPO / "crates" / "target" / "debug" / "engine-worker"
DATASET_MANIFEST = "29bdecf5fe758d38d524025321aacfb2825daf2fbcce4a3c2c04377bf635b97b"

POLL_SECS = 0.5
TIME_CAP_SECS = 120.0


def _load_objective() -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load(OBJECTIVE_PATH.read_text())
    report = validate_spec(spec)
    if not report.ok:
        msg = f"objective spec invalid: {report.errors}"
        raise SystemExit(msg)
    return spec


def _load_bars_payload() -> str:
    return BARS_PATH.read_text()


def _make_evaluator(eng: Engine, base_spec: dict[str, Any], bars_json: str):
    def evaluate(params: dict[str, Any]) -> dict[str, float]:
        spec = deepcopy(base_spec)
        spec["runs"][0]["params"] = {**spec["runs"][0]["params"], **params}
        bars = json.loads(bars_json)
        handle: str = eng._engine.submit_batch(  # noqa: SLF001 — bypass pydantic re-validation per candidate.
            str(ARTIFACT),
            bars_json,
            json.dumps(spec),
            DATASET_MANIFEST,
            None,
        )
        # Bars list reused; suppress pyright unused var.
        _ = bars
        while True:
            status = eng.poll(handle)
            if status.status in ("completed", "failed", "cancelled"):
                break
            time.sleep(POLL_SECS)
        if status.status != "completed":
            return {"sharpe": float("-inf"), "max_drawdown": 1.0, "profit_factor": 0.0}
        assert status.results is not None
        return dict(status.results[0]["metrics"])

    return evaluate


def main() -> None:
    if not ARTIFACT.exists():
        msg = f"artifact missing: {ARTIFACT}. Build with `cargo build -p vxx-strategy`."
        raise SystemExit(msg)
    if not BARS_PATH.exists():
        msg = (
            f"bars missing: {BARS_PATH}. Fetch with "
            "`strategy-gpt fetch --provider yfinance --symbol VXX ...` "
            "and materialize the cache to JSON first."
        )
        raise SystemExit(msg)
    objective = _load_objective()
    base_spec = json.loads(SPEC_PATH.read_text())
    bars_json = _load_bars_payload()

    eng = Engine(WORKER, time_cap_secs=TIME_CAP_SECS)

    # Grid sweep over the realized-vol-20 scale observed on VXX
    # (distribution: min ~0, median ~0.55, max ~2.1).
    searcher = GridSearcher(
        grid={
            "vol_lo": [0.20, 0.30, 0.40, 0.50],
            "vol_hi": [0.70, 0.90, 1.10, 1.30],
        }
    )
    n = searcher.count()
    print(f"running {n} candidates...")

    evaluate = _make_evaluator(eng, base_spec, bars_json)

    native = require_native()

    def score(metrics: dict[str, Any]) -> EvaluationOutcome:
        raw = native.objectives.evaluate_spec(json.dumps(objective), json.dumps(metrics))
        payload = json.loads(raw)
        if payload.get("score") is None:
            payload["score"] = float("-inf")
        return EvaluationOutcome.model_validate(payload)

    started = time.time()
    result = optimize(searcher, evaluate, score)
    elapsed = time.time() - started
    print(f"done in {elapsed:.1f}s. rejected={result.rejected_count}/{n}.")

    ranked = sorted(result.trials, key=lambda t: t.outcome.score, reverse=True)
    print("\nTop 5 by score:")
    for t in ranked[:5]:
        print(
            f"  params={t.params} "
            f"score={t.outcome.score:.4f} accepted={t.accepted} "
            f"sharpe={t.metrics.get('sharpe', float('nan')):.3f} "
            f"max_dd={t.metrics.get('max_drawdown', float('nan')):.3f} "
            f"trades={t.metrics.get('n_trades', 0)}"
        )
    if result.best is None:
        print(
            "\nNo trial passed the objective's constraints. "
            "Check max_drawdown / oos_min_score thresholds."
        )
    else:
        print("\nBest accepted:", json.dumps(result.best.params, indent=2))


if __name__ == "__main__":
    main()
