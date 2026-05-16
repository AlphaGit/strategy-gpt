"""Hypothesis Loop — diagnose node.

Pure-Python analyzer that turns a :class:`BacktestResult` into a structured
:class:`Diagnosis` payload. Consumed by downstream nodes (``kb_query``,
``generate``) as the diagnostic input that shapes the next hypothesis. The
node is deterministic — given the same ``BacktestResult`` it produces the
same diagnosis — which keeps the loop replayable from the ledger.

Scope (`hypothesis-loop::langgraph-workflow-with-explicit-nodes`): analyze
trade clusters, regime performance, and signal misfires.
Trade-cluster summary is win/loss split, P&L extremes, and side split.
Regime performance buckets trades by post-hoc :class:`RegimeTag` ranges and
reports per-label P&L. Signal misfires count fired-but-unused versus
suppressed signals so the LLM can probe under- or over-firing channels.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from .hypothesis_loop import HypothesisLoopState
from .types import (
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
    RegimeTag,
    SignalEvent,
    Trade,
)


class TradeStats(BaseModel):
    """Aggregate trade-cluster statistics."""

    model_config = ConfigDict(frozen=True)

    n_total: int
    n_winners: int
    n_losers: int
    n_breakeven: int
    win_rate: float
    avg_pnl: float
    avg_winner_pnl: float
    avg_loser_pnl: float
    largest_winner_pnl: float
    largest_loser_pnl: float
    total_pnl: float
    total_fees: float
    avg_trade_length_seconds: float
    long_count: int
    short_count: int


class RegimePerformance(BaseModel):
    """Per-regime aggregated trade performance.

    ``label`` is the regime annotation (`low_vol`/`med_vol`/`high_vol` or
    `uptrend`/`downtrend`/`chop`). Trades are bucketed by ``entry_ts``
    falling in any :class:`RegimeTag` range carrying that label. A trade
    that straddles two adjacent ranges is attributed by entry only.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    n_trades: int
    total_pnl: float
    win_rate: float
    avg_pnl: float
    coverage_bars: int


class SignalMisfire(BaseModel):
    """Per-signal firing diagnostics.

    ``fired_no_trade_count`` is ``max(0, fired_count - used_count)``: signals
    that fired but never appeared in any trade's ``signals_at_entry``.
    Strictly an upper bound — a signal can legitimately fire and have its
    name absent from later entries (e.g., a meta-signal). The LLM uses it
    as a directional cue, not a strict count.
    """

    model_config = ConfigDict(frozen=True)

    signal: str
    fired_count: int
    suppressed_count: int
    used_count: int
    fired_no_trade_count: int
    suppression_reasons: dict[str, int] = Field(default_factory=dict)


class Diagnosis(BaseModel):
    """Diagnose-node output. Carried as ``HypothesisLoopState.diagnosis``."""

    model_config = ConfigDict(frozen=True)

    metrics: BacktestMetrics
    trade_stats: TradeStats
    regime_performance: list[RegimePerformance]
    signal_misfires: list[SignalMisfire]
    exec_log_summary: dict[str, int]


def _trade_stats(trades: list[Trade]) -> TradeStats:
    if not trades:
        return TradeStats(
            n_total=0,
            n_winners=0,
            n_losers=0,
            n_breakeven=0,
            win_rate=0.0,
            avg_pnl=0.0,
            avg_winner_pnl=0.0,
            avg_loser_pnl=0.0,
            largest_winner_pnl=0.0,
            largest_loser_pnl=0.0,
            total_pnl=0.0,
            total_fees=0.0,
            avg_trade_length_seconds=0.0,
            long_count=0,
            short_count=0,
        )
    pnls = [t.pnl for t in trades]
    winners = [p for p in pnls if p > 0.0]
    losers = [p for p in pnls if p < 0.0]
    breakeven = len(pnls) - len(winners) - len(losers)
    lengths = [(t.exit_ts - t.entry_ts).total_seconds() for t in trades]
    long_count = sum(1 for t in trades if t.side.value == "Long")
    short_count = len(trades) - long_count
    return TradeStats(
        n_total=len(trades),
        n_winners=len(winners),
        n_losers=len(losers),
        n_breakeven=breakeven,
        win_rate=len(winners) / len(trades),
        avg_pnl=sum(pnls) / len(pnls),
        avg_winner_pnl=(sum(winners) / len(winners)) if winners else 0.0,
        avg_loser_pnl=(sum(losers) / len(losers)) if losers else 0.0,
        largest_winner_pnl=max(pnls),
        largest_loser_pnl=min(pnls),
        total_pnl=sum(pnls),
        total_fees=sum(t.fees for t in trades),
        avg_trade_length_seconds=sum(lengths) / len(lengths),
        long_count=long_count,
        short_count=short_count,
    )


def _regime_performance(
    trades: list[Trade],
    equity: list[EquityPoint],
    regimes: list[RegimeTag],
) -> list[RegimePerformance]:
    if not regimes:
        return []
    # Group tags by label so coverage_bars is summed across disjoint ranges
    # that share a label (annotate_regimes emits one tag per contiguous
    # run, so the same label appears multiple times across the timeline).
    by_label: dict[str, list[RegimeTag]] = defaultdict(list)
    for tag in regimes:
        by_label[tag.label].append(tag)

    out: list[RegimePerformance] = []
    for label in sorted(by_label):
        ranges = by_label[label]
        bucket: list[Trade] = []
        for trade in trades:
            for tag in ranges:
                if tag.start <= trade.entry_ts < tag.end:
                    bucket.append(trade)
                    break
        coverage = 0
        for point in equity:
            for tag in ranges:
                if tag.start <= point.ts < tag.end:
                    coverage += 1
                    break
        n = len(bucket)
        if n == 0:
            out.append(
                RegimePerformance(
                    label=label,
                    n_trades=0,
                    total_pnl=0.0,
                    win_rate=0.0,
                    avg_pnl=0.0,
                    coverage_bars=coverage,
                )
            )
            continue
        pnls = [t.pnl for t in bucket]
        wins = sum(1 for p in pnls if p > 0.0)
        out.append(
            RegimePerformance(
                label=label,
                n_trades=n,
                total_pnl=sum(pnls),
                win_rate=wins / n,
                avg_pnl=sum(pnls) / n,
                coverage_bars=coverage,
            )
        )
    return out


def _signal_misfires(signals: list[SignalEvent], trades: list[Trade]) -> list[SignalMisfire]:
    fired_counts: Counter[str] = Counter()
    suppressed_counts: Counter[str] = Counter()
    suppress_reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for s in signals:
        if s.fired:
            fired_counts[s.name] += 1
        elif s.suppressed_by is not None:
            suppressed_counts[s.name] += 1
            suppress_reasons[s.name][s.suppressed_by] += 1

    used_counts: Counter[str] = Counter()
    for t in trades:
        for name in t.signals_at_entry:
            used_counts[name] += 1

    names = set(fired_counts) | set(suppressed_counts) | set(used_counts)
    out: list[SignalMisfire] = []
    for name in sorted(names):
        fired = fired_counts[name]
        suppressed = suppressed_counts[name]
        used = used_counts[name]
        out.append(
            SignalMisfire(
                signal=name,
                fired_count=fired,
                suppressed_count=suppressed,
                used_count=used,
                fired_no_trade_count=max(0, fired - used),
                suppression_reasons=dict(suppress_reasons.get(name, Counter())),
            )
        )
    return out


def diagnose(result: BacktestResult) -> Diagnosis:
    """Compute a structured :class:`Diagnosis` for a backtest result.

    Pure / deterministic / cheap — safe to call repeatedly. The output is
    state that the ``generate`` node prompt consumes; storing it on the
    :class:`HypothesisLoopState` keeps the LangGraph workflow's iteration
    state self-contained without re-reading the (potentially large)
    ``BacktestResult``.
    """
    exec_summary: dict[str, int] = dict(Counter(ev.event for ev in result.exec_log))
    return Diagnosis(
        metrics=result.metrics,
        trade_stats=_trade_stats(result.trades),
        regime_performance=_regime_performance(result.trades, result.equity, result.regimes),
        signal_misfires=_signal_misfires(result.signals, result.trades),
        exec_log_summary=exec_summary,
    )


def diagnose_node(state: HypothesisLoopState, result: BacktestResult) -> HypothesisLoopState:
    """LangGraph-style node: attach the diagnosis to the loop state.

    The full graph wiring lands in 9.8; for now the node is a function
    callable directly from tests and the orchestrator entry point.
    """
    diagnosis = diagnose(result)
    return state.model_copy(update={"diagnosis": diagnosis})


__all__ = [
    "Diagnosis",
    "RegimePerformance",
    "SignalMisfire",
    "TradeStats",
    "diagnose",
    "diagnose_node",
]
