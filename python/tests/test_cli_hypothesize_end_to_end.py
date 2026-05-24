"""CLI tests for ``strategy-gpt hypothesize``.

The wiring helpers and orchestrator entry are monkeypatched so the CLI
contract (flag parsing, validation gates, JSON envelope shape) is
covered without booting the native engine or making LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from strategy_gpt import cli
from strategy_gpt import hypothesize as hyp_mod
from strategy_gpt import hypothesize_wiring as hw
from strategy_gpt.hypothesis_loop import TerminationReason
from strategy_gpt.types import BacktestMetrics, BacktestResult, ResultMeta, RunnerVersion

runner = CliRunner()


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

_INTENT_TOML = """\
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
{"atr_window": {"kind": "i64", "min": 5, "max": 50, "default": 14}}
'''
"""

_SMOKE_TOML = (
    'symbol = "SPY"\n'
    'resolution = "1d"\n'
    'start = "2023-01-01"\n'
    'end = "2023-03-01"\n'
    'provider = "yfinance"\n'
)


@pytest.fixture
def repo_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    crate = tmp_path / "crates" / "spy_atr-strategy"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(_CARGO_TOML)
    (crate / "src/lib.rs").write_text("// noop\n")
    (crate / "intent.toml").write_text(_INTENT_TOML)
    (crate / "smoke.toml").write_text(_SMOKE_TOML)

    # Required by the CLI's engine-worker check
    worker = tmp_path / "crates" / "target" / "debug" / "engine-worker"
    worker.parent.mkdir(parents=True)
    worker.write_text("#!/bin/sh\n")
    worker.chmod(0o755)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _fake_baseline_tuple(source: str) -> hw.BaselineTuple:
    metrics = BacktestMetrics(
        sharpe=1.0,
        sortino=1.1,
        profit_factor=1.5,
        win_ratio=0.55,
        max_drawdown=0.1,
        annualized_return=0.2,
        n_trades=20,
        avg_trade_length_bars=5.0,
    )
    result = BacktestResult(
        meta=ResultMeta(
            strategy_artifact="fake",
            dataset_manifest="manifest-fake",
            seed=0,
            runner_version=RunnerVersion(major=0, minor=1, patch=0),
        ),
        metrics=metrics,
        trades=[],
        signals=[],
        equity=[],
        exec_log=[],
    )
    return hw.BaselineTuple(
        result=result,
        files={"Cargo.toml": _CARGO_TOML},
        params_schema=None,
        per_fold_scores=[1.0],
        metrics={"sharpe": 1.0},
        aggregate_score=1.0,
        source=source,
    )


def _patch_wiring(monkeypatch: pytest.MonkeyPatch, baseline_source: str) -> dict[str, int]:
    """Monkeypatch the wiring helpers with no-op stubs.

    Returns a counter dict that records how many times each stub was
    invoked so the tests can assert call shapes.
    """
    counters: dict[str, int] = {"build_kb": 0, "build_stage": 0, "build_eval": 0, "hypothesize": 0}

    def _evaluator(params: Any, fold_idx: int) -> BacktestMetrics:
        del params, fold_idx
        return BacktestMetrics(
            sharpe=1.0,
            sortino=1.1,
            profit_factor=1.5,
            win_ratio=0.55,
            max_drawdown=0.1,
            annualized_return=0.2,
            n_trades=20,
            avg_trade_length_bars=5.0,
        )

    def _fake_kb(*_args: Any, **_kwargs: Any) -> object:
        counters["build_kb"] += 1

        class _Result:
            def __init__(self) -> None:
                self.items: list[Any] = []

        class _StubKb:
            def retrieve(self, query: str, k: int) -> Any:
                del query, k
                return _Result()

        return _StubKb()

    def _fake_stage(*_args: Any, **_kwargs: Any) -> object:
        counters["build_stage"] += 1
        return object()

    def _fake_eval(*_args: Any, **_kwargs: Any) -> tuple[Any, str, int]:
        counters["build_eval"] += 1
        return _evaluator, "manifest-stub", 1

    def _fake_baseline_defaults(*_args: Any, **_kwargs: Any) -> hw.BaselineTuple:
        return _fake_baseline_tuple("baseline_defaults")

    def _fake_baseline_from(opt_id: str, *_args: Any, **_kwargs: Any) -> hw.BaselineTuple:
        return _fake_baseline_tuple(f"optimize_run:{opt_id}")

    def _fake_hypothesize(*_args: Any, **kwargs: Any) -> hyp_mod.HypothesizeResult:
        counters["hypothesize"] += 1
        return hyp_mod.HypothesizeResult(
            strategy=kwargs.get("strategy", "spy_atr"),
            accepted=[],
            rejected=[],
            termination_reason=TerminationReason.SUFFICIENT_CANDIDATES,
            iterations=0,
            backtests_consumed=0,
            persisted_decision_ids=[],
            state={},  # type: ignore[typeddict-item]
        )

    # The CLI imports BuildPipeline lazily; stub it to avoid the native bind.
    class _StubPipeline:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def lint(self, *_args: Any, **_kwargs: Any) -> Any:
            return None

        def build(self, *_args: Any, **_kwargs: Any) -> Any:
            return None

    monkeypatch.setattr("strategy_gpt.build_pipeline.BuildPipeline", _StubPipeline)
    monkeypatch.setattr(hw, "build_kb_client", _fake_kb)
    monkeypatch.setattr(hw, "build_stage_client", _fake_stage)
    monkeypatch.setattr(hw, "build_evaluate_fold", _fake_eval)
    monkeypatch.setattr(hw, "compute_baseline_defaults", _fake_baseline_defaults)
    monkeypatch.setattr(hw, "load_baseline_from_optimize", _fake_baseline_from)
    monkeypatch.setattr(hyp_mod, "hypothesize", _fake_hypothesize)
    return counters


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_hypothesize_baseline_defaults_emits_envelope(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counters = _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(
        cli.app,
        [
            "hypothesize",
            "spy_atr",
            "--baseline-defaults",
            "--engine-worker",
            str(repo_layout / "crates" / "target" / "debug" / "engine-worker"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["strategy"] == "spy_atr"
    assert payload["termination_reason"] == "sufficient_candidates"
    assert payload["baseline_source"] == "baseline_defaults"
    assert counters["hypothesize"] == 1


def test_hypothesize_baseline_from_optimize(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_wiring(monkeypatch, "optimize_run:opt-fixture")
    result = runner.invoke(
        cli.app,
        [
            "hypothesize",
            "spy_atr",
            "--baseline-from",
            "opt-fixture",
            "--engine-worker",
            str(repo_layout / "crates" / "target" / "debug" / "engine-worker"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["baseline_source"] == "optimize_run:opt-fixture"


def test_hypothesize_dry_run_does_not_invoke_workflow(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counters = _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(
        cli.app,
        [
            "hypothesize",
            "spy_atr",
            "--baseline-defaults",
            "--dry-run",
            "--engine-worker",
            str(repo_layout / "crates" / "target" / "debug" / "engine-worker"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["baseline_source"] == "baseline_defaults"
    assert counters["hypothesize"] == 0


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_hypothesize_missing_crate(repo_layout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(
        cli.app,
        ["hypothesize", "nope", "--baseline-defaults"],
    )
    assert result.exit_code == 2
    assert "does not exist" in result.stderr or "does not exist" in result.stdout


def test_hypothesize_missing_intent_toml(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    (repo_layout / "crates" / "spy_atr-strategy" / "intent.toml").unlink()
    result = runner.invoke(cli.app, ["hypothesize", "spy_atr", "--baseline-defaults"])
    assert result.exit_code == 2


def test_hypothesize_no_baseline_flag(repo_layout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(cli.app, ["hypothesize", "spy_atr"])
    assert result.exit_code == 2
    text = result.stderr or result.stdout
    assert "baseline" in text.lower()


def test_hypothesize_missing_api_key(repo_layout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["hypothesize", "spy_atr", "--baseline-defaults"])
    assert result.exit_code == 2
    text = result.stderr or result.stdout
    assert "API_KEY" in text


def test_hypothesize_missing_engine_worker(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(
        cli.app,
        [
            "hypothesize",
            "spy_atr",
            "--baseline-defaults",
            "--engine-worker",
            str(repo_layout / "no-binary-here"),
        ],
    )
    assert result.exit_code == 2
    text = result.stderr or result.stdout
    assert "engine-worker" in text


def test_hypothesize_baseline_flags_mutually_exclusive(
    repo_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_wiring(monkeypatch, "baseline_defaults")
    result = runner.invoke(
        cli.app,
        [
            "hypothesize",
            "spy_atr",
            "--baseline-defaults",
            "--baseline-from",
            "opt-id",
        ],
    )
    assert result.exit_code != 0
