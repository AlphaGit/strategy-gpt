"""Unit tests for :mod:`strategy_gpt.hypothesize_wiring`.

Construction helpers are pure functions of inputs (crate paths, env,
optimize-run id, ...). These tests exercise the helpers directly so the
CLI's pre-workflow contract is covered without booting typer or the
native engine.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from strategy_gpt import hypothesize_wiring as hw
from strategy_gpt.optimizer import ContinuousParam, IntParam
from strategy_gpt.reasoning import ReasoningModel
from strategy_gpt.types import BacktestMetrics

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CARGO_TOML = """\
[package]
name = "spy_atr-strategy"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
engine-rt = { path = "../engine-rt" }
"""

_LIB_RS = "// no-op body\n"

_PARAM_JSON = (
    '{"atr_window": {"kind": "i64", "min": 5, "max": 50, "default": 14}, '
    '"stop_mult": {"kind": "f64", "min": 1.0, "max": 5.0, "default": 2.0}}'
)

_INTENT_TOML = f"""\
name = "spy_atr"
description = "ATR breakout."
mechanism_summary = "ATR breakout strategy."

[smoke_spec]
symbol = "SPY"
resolution = "1d"
start = "2023-01-01"
end = "2023-03-01"
provider = "yfinance"

[param_schema_sketch]
_json = '''
{_PARAM_JSON}
'''
"""

_SMOKE_TOML = """\
symbol = "SPY"
resolution = "1d"
start = "2023-01-01"
end = "2023-03-01"
provider = "yfinance"
"""


def _make_crate(tmp_path: Path, *, with_experiment: bool = False) -> Path:
    crates = tmp_path / "crates"
    crate = crates / "spy_atr-strategy"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(_CARGO_TOML)
    (crate / "src/lib.rs").write_text(_LIB_RS)
    (crate / "intent.toml").write_text(_INTENT_TOML)
    (crate / "smoke.toml").write_text(_SMOKE_TOML)
    if with_experiment:
        (crate / "experiment.yaml").write_text("# placeholder\n")
    return crates


# ---------------------------------------------------------------------------
# resolve_crate_paths
# ---------------------------------------------------------------------------


def test_resolve_crate_paths_happy(tmp_path: Path) -> None:
    crates = _make_crate(tmp_path)
    paths = hw.resolve_crate_paths("spy_atr", crates)
    assert paths.crate_dir == (crates / "spy_atr-strategy").resolve()
    assert paths.cargo_toml.is_file()
    assert paths.lib_rs.is_file()
    assert paths.intent_toml.is_file()
    assert paths.smoke_toml.is_file()
    assert paths.experiment_yaml is None


def test_resolve_crate_paths_with_experiment(tmp_path: Path) -> None:
    crates = _make_crate(tmp_path, with_experiment=True)
    paths = hw.resolve_crate_paths("spy_atr", crates)
    assert paths.experiment_yaml is not None
    assert paths.experiment_yaml.name == "experiment.yaml"


def test_resolve_crate_paths_missing_crate(tmp_path: Path) -> None:
    with pytest.raises(hw.MissingArtifactError, match="does not exist"):
        hw.resolve_crate_paths("missing", tmp_path / "crates")


def test_resolve_crate_paths_missing_intent(tmp_path: Path) -> None:
    crates = _make_crate(tmp_path)
    (crates / "spy_atr-strategy" / "intent.toml").unlink()
    with pytest.raises(hw.MissingArtifactError, match=r"intent\.toml"):
        hw.resolve_crate_paths("spy_atr", crates)


# ---------------------------------------------------------------------------
# resolve_kept_bounds
# ---------------------------------------------------------------------------


def test_resolve_kept_bounds_mixed_kinds() -> None:
    schema = {
        "atr_window": {"kind": "i64", "min": 5, "max": 50, "default": 14},
        "stop_mult": {"kind": "f64", "min": 1.0, "max": 5.0, "default": 2.0},
        "no_bounds": {"kind": "f64", "default": 1.0},
    }
    bounds = hw.resolve_kept_bounds(schema)
    assert isinstance(bounds["atr_window"], IntParam)
    assert bounds["atr_window"].low == 5
    assert bounds["atr_window"].high == 50
    assert isinstance(bounds["stop_mult"], ContinuousParam)
    assert "no_bounds" not in bounds


# ---------------------------------------------------------------------------
# resolve_objective_metric
# ---------------------------------------------------------------------------


def test_resolve_objective_metric_precedence() -> None:
    intent: Mapping[str, Any] = {"objective_metric": "sortino"}
    assert hw.resolve_objective_metric(intent, override=None) == "sortino"
    assert hw.resolve_objective_metric(intent, override="profit_factor") == "profit_factor"
    assert hw.resolve_objective_metric({}, override=None) == "sharpe"


# ---------------------------------------------------------------------------
# verify_api_keys
# ---------------------------------------------------------------------------


def test_verify_api_keys_with_anthropic_set() -> None:
    hw.verify_api_keys({"ANTHROPIC_API_KEY": "sk-test"})


def test_verify_api_keys_missing() -> None:
    with pytest.raises(hw.MissingApiKeyError):
        hw.verify_api_keys({})


# ---------------------------------------------------------------------------
# compute_baseline_defaults
# ---------------------------------------------------------------------------


def _stub_metrics(score: float) -> BacktestMetrics:
    return BacktestMetrics(
        sharpe=score,
        sortino=score * 1.1,
        profit_factor=1.5,
        win_ratio=0.55,
        max_drawdown=0.1,
        annualized_return=0.2,
        n_trades=20,
        avg_trade_length_bars=5.0,
    )


def test_compute_baseline_defaults_emits_progress_per_fold(tmp_path: Path) -> None:
    """Baseline computation runs strategies per fold — operator MUST
    see metrics for each one or the loop appears stalled while engine
    subprocesses execute.
    """
    crates = _make_crate(tmp_path)
    paths = hw.resolve_crate_paths("spy_atr", crates)

    def evaluator(params: Mapping[str, Any], fold_idx: int) -> BacktestMetrics:
        del params
        return _stub_metrics(1.0 + fold_idx * 0.1)

    events: list[str] = []
    hw.compute_baseline_defaults(
        paths,
        evaluator,
        fold_count=2,
        progress_sink=events.append,
    )
    joined = "\n".join(events)
    assert "baseline_defaults: running fold 1/2" in joined
    assert "baseline_defaults: fold 1/2 done" in joined
    assert "baseline_defaults: running fold 2/2" in joined
    assert "sharpe=" in joined


def test_compute_baseline_defaults_lifts_defaults_and_per_fold(tmp_path: Path) -> None:
    crates = _make_crate(tmp_path)
    paths = hw.resolve_crate_paths("spy_atr", crates)

    seen_params: list[Mapping[str, Any]] = []
    seen_folds: list[int] = []

    def evaluator(params: Mapping[str, Any], fold_idx: int) -> BacktestMetrics:
        seen_params.append(dict(params))
        seen_folds.append(fold_idx)
        return _stub_metrics(1.0 + fold_idx * 0.1)

    baseline = hw.compute_baseline_defaults(paths, evaluator, fold_count=3)

    assert baseline.source == "baseline_defaults"
    assert seen_folds == [0, 1, 2]
    assert seen_params == [{"atr_window": 14, "stop_mult": 2.0}] * 3
    assert baseline.per_fold_scores == [1.0, 1.1, 1.2]
    assert baseline.aggregate_score == pytest.approx(1.1)
    assert "Cargo.toml" in baseline.files
    assert baseline.metrics["sharpe"] == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# load_baseline_from_optimize
# ---------------------------------------------------------------------------


def _write_optimize_fixture(ledger_root: Path, opt_id: str) -> Path:
    opt_dir = ledger_root / "optimizations" / opt_id
    opt_dir.mkdir(parents=True)
    manifest = {
        "opt_id": opt_id,
        "dataset_manifest": "manifest-deadbeef",
        "artifact_path": str(ledger_root / "strategy.dylib"),
    }
    (opt_dir / "manifest.json").write_text(json.dumps(manifest))
    best = {
        "opt_id": opt_id,
        "final": {
            "fold_index": 0,
            "params": {"atr_window": 21, "stop_mult": 2.5},
            "aggregate_score": 1.25,
            "aggregate_metrics": {
                "sharpe": 1.25,
                "sortino": 1.4,
                "profit_factor": 1.6,
                "win_ratio": 0.6,
                "max_drawdown": 0.09,
                "annualized_return": 0.22,
                "n_trades": 80,
                "avg_trade_length_bars": 5.5,
            },
            "oos_metrics": [
                {"sharpe": 1.1, "n_trades": 25},
                {"sharpe": 1.3, "n_trades": 27},
                {"sharpe": 1.35, "n_trades": 28},
            ],
        },
    }
    (opt_dir / "best.json").write_text(json.dumps(best))
    return opt_dir


def test_load_baseline_from_optimize_roundtrip(tmp_path: Path) -> None:
    crates = _make_crate(tmp_path)
    paths = hw.resolve_crate_paths("spy_atr", crates)
    _write_optimize_fixture(tmp_path / "ledger", "opt-fixture")

    baseline = hw.load_baseline_from_optimize(
        "opt-fixture",
        tmp_path / "ledger",
        crate_paths=paths,
        objective_metric="sharpe",
    )
    assert baseline.source == "optimize_run:opt-fixture"
    assert baseline.aggregate_score == pytest.approx(1.25)
    assert baseline.per_fold_scores == [1.1, 1.3, 1.35]
    assert baseline.metrics["sharpe"] == pytest.approx(1.25)
    assert "Cargo.toml" in baseline.files
    assert baseline.result.meta.dataset_manifest == "manifest-deadbeef"


def test_load_baseline_from_optimize_missing(tmp_path: Path) -> None:
    with pytest.raises(hw.MissingOptimizeRunError):
        hw.load_baseline_from_optimize("not-here", tmp_path / "ledger")


def test_load_baseline_from_optimize_no_best(tmp_path: Path) -> None:
    opt_dir = tmp_path / "ledger" / "optimizations" / "opt-empty"
    opt_dir.mkdir(parents=True)
    (opt_dir / "manifest.json").write_text("{}")
    with pytest.raises(hw.MissingArtifactError, match=r"best\.json"):
        hw.load_baseline_from_optimize("opt-empty", tmp_path / "ledger")


# ---------------------------------------------------------------------------
# build_kb_client
# ---------------------------------------------------------------------------


def test_build_kb_client_missing_sources(tmp_path: Path) -> None:
    store = tmp_path / "store"
    sources = tmp_path / "nope.toml"
    with pytest.raises(hw.MissingArtifactError):
        hw.build_kb_client(store, sources)


# ---------------------------------------------------------------------------
# Evaluate-fold experiment.yaml fold derivation (no engine required)
# ---------------------------------------------------------------------------


def test_derive_fold_slices_from_experiment_smoke_fallback(tmp_path: Path) -> None:
    # When the experiment.yaml has no `folds` block, we fall back to a
    # single (start, end) pair from the first run.
    experiment_yaml = tmp_path / "experiment.yaml"
    experiment_yaml.write_text(
        """\
artifact: ./strategy.dylib
bars:
  request:
    provider: yfinance
    symbol: SPY
    start: 2023-01-01T00:00:00Z
    end: 2024-01-01T00:00:00Z
    resolution: Day
    adjustment: back_adjusted
runs:
  - params: {}
    slice: {start: 2023-01-01T00:00:00Z, end: 2024-01-01T00:00:00Z}
"""
    )
    slices = hw._derive_fold_slices_from_experiment(experiment_yaml)
    assert len(slices) == 1


# ---------------------------------------------------------------------------
# Stage router
# ---------------------------------------------------------------------------


def test_stage_router_routes_per_stage() -> None:
    seen: list[tuple[int, str]] = []

    class _Dispatch:
        def emit_stage(
            self,
            *,
            prompt: Any,
            stage: int,
            model: ReasoningModel,
            max_tokens: int = 8192,
            temperature: float = 0.7,
        ) -> str:
            del prompt, max_tokens, temperature
            seen.append((stage, model.model_id))
            return ""

    router = hw._StageRouter(
        dispatch=_Dispatch(),  # type: ignore[arg-type]
        stage_models={
            "stage1": ReasoningModel(provider="anthropic", model_id="claude-opus-4-7"),
            "stage2": ReasoningModel(provider="anthropic", model_id="claude-sonnet-4-6"),
            "stage3": ReasoningModel(provider="anthropic", model_id="claude-haiku-4-5-20251001"),
            "critique": ReasoningModel(provider="anthropic", model_id="claude-haiku-4-5-20251001"),
            "rank": ReasoningModel(provider="anthropic", model_id="claude-haiku-4-5-20251001"),
        },
    )
    default = ReasoningModel(provider="anthropic", model_id="default-model")
    for stage in (1, 2, 3):
        router.emit_stage(prompt=None, stage=stage, model=default)  # type: ignore[arg-type]
    assert seen == [
        (1, "claude-opus-4-7"),
        (2, "claude-sonnet-4-6"),
        (3, "claude-haiku-4-5-20251001"),
    ]
