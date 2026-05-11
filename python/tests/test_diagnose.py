"""Diagnose-node tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from strategy_gpt.diagnose import (
    Diagnosis,
    diagnose,
    diagnose_node,
)
from strategy_gpt.hypothesis_loop import HypothesisLoopState
from strategy_gpt.types import (
    BacktestMetrics,
    BacktestResult,
    DecisionEvent,
    EquityPoint,
    RegimeTag,
    ResultMeta,
    RunnerVersion,
    Side,
    SignalEvent,
    Trade,
)


def _meta() -> ResultMeta:
    return ResultMeta(
        strategy_artifact="a1",
        dataset_manifest="d1",
        seed=1,
        runner_version=RunnerVersion(major=1, minor=0, patch=0),
    )


def _metrics() -> BacktestMetrics:
    return BacktestMetrics(
        sharpe=1.2,
        sortino=1.5,
        profit_factor=1.4,
        win_ratio=0.55,
        max_drawdown=0.08,
        annualized_return=0.18,
        n_trades=5,
        avg_trade_length_bars=4.0,
    )


def _trade(  # noqa: PLR0913 — explicit kwargs for readability
    *,
    entry: datetime,
    exit_: datetime,
    side: Side = Side.LONG,
    pnl: float = 1.0,
    fees: float = 0.1,
    signals: list[str] | None = None,
) -> Trade:
    return Trade(
        entry_ts=entry,
        exit_ts=exit_,
        symbol="VXX",
        side=side,
        size=1.0,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        fees=fees,
        signals_at_entry=signals or [],
    )


def _result(
    *,
    trades: list[Trade],
    signals: list[SignalEvent],
    equity: list[EquityPoint],
    regimes: list[RegimeTag],
    exec_log: list[DecisionEvent] | None = None,
) -> BacktestResult:
    return BacktestResult(
        meta=_meta(),
        metrics=_metrics(),
        trades=trades,
        signals=signals,
        equity=equity,
        exec_log=exec_log or [],
        regimes=regimes,
    )


def test_empty_result_yields_zeroed_trade_stats() -> None:
    r = _result(trades=[], signals=[], equity=[], regimes=[])
    d = diagnose(r)
    assert d.trade_stats.n_total == 0
    assert d.trade_stats.win_rate == 0.0
    assert d.regime_performance == []
    assert d.signal_misfires == []
    assert d.exec_log_summary == {}
    assert d.metrics == r.metrics


def test_trade_stats_compute_winners_losers_sides() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    trades = [
        _trade(entry=t0, exit_=t0 + timedelta(hours=2), pnl=2.5, fees=0.1),
        _trade(entry=t0, exit_=t0 + timedelta(hours=4), pnl=-1.0, fees=0.2),
        _trade(entry=t0, exit_=t0 + timedelta(hours=3), pnl=0.0, fees=0.05),
        _trade(
            entry=t0,
            exit_=t0 + timedelta(hours=1),
            side=Side.SHORT,
            pnl=0.7,
            fees=0.1,
        ),
    ]
    d = diagnose(_result(trades=trades, signals=[], equity=[], regimes=[]))
    s = d.trade_stats
    assert s.n_total == 4
    assert s.n_winners == 2  # 2.5, 0.7
    assert s.n_losers == 1
    assert s.n_breakeven == 1
    assert s.win_rate == pytest.approx(0.5)
    assert s.long_count == 3
    assert s.short_count == 1
    assert s.largest_winner_pnl == pytest.approx(2.5)
    assert s.largest_loser_pnl == pytest.approx(-1.0)
    assert s.total_pnl == pytest.approx(2.2)
    assert s.total_fees == pytest.approx(0.45)
    assert s.avg_trade_length_seconds == pytest.approx((2 + 4 + 3 + 1) * 3600 / 4)


def test_regime_performance_buckets_by_entry_ts() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    regimes = [
        RegimeTag(start=t0, end=t0 + timedelta(hours=2), label="low_vol"),
        RegimeTag(
            start=t0 + timedelta(hours=2),
            end=t0 + timedelta(hours=6),
            label="high_vol",
        ),
        RegimeTag(
            start=t0 + timedelta(hours=6),
            end=t0 + timedelta(hours=8),
            label="low_vol",
        ),
    ]
    equity = [
        EquityPoint(ts=t0 + timedelta(hours=h), equity=1.0, drawdown=0.0, exposure=0.0)
        for h in range(8)
    ]
    trades = [
        _trade(entry=t0 + timedelta(hours=1), exit_=t0 + timedelta(hours=2), pnl=1.0),
        _trade(entry=t0 + timedelta(hours=3), exit_=t0 + timedelta(hours=5), pnl=-2.0),
        _trade(entry=t0 + timedelta(hours=7), exit_=t0 + timedelta(hours=8), pnl=0.5),
    ]
    d = diagnose(_result(trades=trades, signals=[], equity=equity, regimes=regimes))
    perf = {p.label: p for p in d.regime_performance}
    assert set(perf) == {"low_vol", "high_vol"}
    low = perf["low_vol"]
    assert low.n_trades == 2
    assert low.total_pnl == pytest.approx(1.5)
    assert low.win_rate == pytest.approx(1.0)
    assert low.coverage_bars == 4  # hours 0,1,6,7
    high = perf["high_vol"]
    assert high.n_trades == 1
    assert high.total_pnl == pytest.approx(-2.0)
    assert high.win_rate == 0.0
    assert high.coverage_bars == 4  # hours 2,3,4,5


def test_signal_misfires_count_fired_suppressed_and_used() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    signals = [
        SignalEvent(name="a", ts=t0, value=1.0, fired=True, suppressed_by=None),
        SignalEvent(name="a", ts=t0, value=1.0, fired=True, suppressed_by=None),
        SignalEvent(name="a", ts=t0, value=1.0, fired=True, suppressed_by=None),
        SignalEvent(name="a", ts=t0, value=0.0, fired=False, suppressed_by="cooldown"),
        SignalEvent(name="b", ts=t0, value=0.5, fired=False, suppressed_by="cooldown"),
        SignalEvent(name="b", ts=t0, value=0.5, fired=False, suppressed_by="cooldown"),
        SignalEvent(name="b", ts=t0, value=0.5, fired=False, suppressed_by="regime"),
    ]
    trades = [
        _trade(entry=t0, exit_=t0 + timedelta(hours=1), signals=["a"]),
        _trade(entry=t0, exit_=t0 + timedelta(hours=1), signals=["a"]),
    ]
    d = diagnose(_result(trades=trades, signals=signals, equity=[], regimes=[]))
    by_name = {m.signal: m for m in d.signal_misfires}
    assert set(by_name) == {"a", "b"}
    a = by_name["a"]
    assert a.fired_count == 3
    assert a.suppressed_count == 1
    assert a.used_count == 2
    assert a.fired_no_trade_count == 1
    assert a.suppression_reasons == {"cooldown": 1}
    b = by_name["b"]
    assert b.fired_count == 0
    assert b.suppressed_count == 3
    assert b.used_count == 0
    assert b.fired_no_trade_count == 0
    assert b.suppression_reasons == {"cooldown": 2, "regime": 1}


def test_exec_log_summary_counts_event_kinds() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    exec_log = [
        DecisionEvent(ts=t0, event="submit", details={}),
        DecisionEvent(ts=t0, event="submit", details={}),
        DecisionEvent(ts=t0, event="reject_sanity", details={}),
    ]
    d = diagnose(_result(trades=[], signals=[], equity=[], regimes=[], exec_log=exec_log))
    assert d.exec_log_summary == {"submit": 2, "reject_sanity": 1}


def test_diagnose_is_deterministic_on_repeated_calls() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    r = _result(
        trades=[_trade(entry=t0, exit_=t0 + timedelta(hours=1), pnl=1.0)],
        signals=[SignalEvent(name="a", ts=t0, value=1.0, fired=True)],
        equity=[EquityPoint(ts=t0, equity=1.0, drawdown=0.0, exposure=0.0)],
        regimes=[],
    )
    a = diagnose(r)
    b = diagnose(r)
    assert a == b
    assert a.model_dump_json() == b.model_dump_json()


def test_diagnose_node_attaches_diagnosis_to_state() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    state = HypothesisLoopState()
    r = _result(
        trades=[_trade(entry=t0, exit_=t0 + timedelta(hours=1), pnl=1.0)],
        signals=[],
        equity=[],
        regimes=[],
    )
    new_state = diagnose_node(state, r)
    assert isinstance(new_state.diagnosis, Diagnosis)
    assert new_state.diagnosis.trade_stats.n_total == 1
    # Original state untouched.
    assert state.diagnosis is None


def test_backtest_result_json_round_trip_via_mirror() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    r = _result(
        trades=[_trade(entry=t0, exit_=t0 + timedelta(hours=1), pnl=1.0)],
        signals=[SignalEvent(name="a", ts=t0, value=1.0, fired=False, suppressed_by="x")],
        equity=[EquityPoint(ts=t0, equity=1.0, drawdown=0.0, exposure=0.0)],
        regimes=[RegimeTag(start=t0, end=t0 + timedelta(hours=2), label="low_vol")],
    )
    payload = r.model_dump_json()
    restored = BacktestResult.model_validate_json(payload)
    assert restored == r
