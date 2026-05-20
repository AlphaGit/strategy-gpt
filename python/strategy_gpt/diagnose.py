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
from datetime import datetime

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


class DrawdownEpisode(BaseModel):
    """One drawdown trough plus its peak-to-trough and recovery duration."""

    model_config = ConfigDict(frozen=True)

    trough_ts: datetime
    depth: float
    duration_bars: int
    recovered: bool


class DrawdownTrajectory(BaseModel):
    """Drawdown trajectory shape summary.

    ``shape`` is one of ``flat`` (no drawdowns), ``shallow``,
    ``moderate``, ``deep`` — a coarse categorical label the LLM can read
    in its prompt without parsing numerical depth thresholds. ``episodes``
    enumerates the top-K troughs by depth so the LLM can reason about
    isolated vs. clustered drawdowns.
    """

    model_config = ConfigDict(frozen=True)

    shape: str
    max_depth: float
    n_episodes: int
    longest_duration_bars: int
    episodes: list[DrawdownEpisode] = Field(default_factory=list)


class HoldingPeriodBucket(BaseModel):
    """One bucket of the holding-period x pnl histogram."""

    model_config = ConfigDict(frozen=True)

    bucket: str
    n_trades: int
    avg_pnl: float
    total_pnl: float


class MissedOpportunityRegion(BaseModel):
    """A timeline window where signal pressure existed but no trade fired.

    The LLM uses these as candidate regions for new entry logic — a
    region with high ``suppression_density`` and zero trades is a strong
    candidate for "loosen suppression here" or "alternative entry
    channel" hypotheses.
    """

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    suppression_density: float
    fired_no_trade: int
    n_trades_in_region: int


class Diagnosis(BaseModel):
    """Diagnose-node output. Carried as ``HypothesisLoopState.diagnosis``.

    Extended in Phase C with:

    - ``exit_reason_histogram`` — counts of trade ``reason_out`` values,
      so the LLM can reason about which exit channel dominates.
    - ``drawdown_trajectory`` — coarse shape categorization + top
      drawdown episodes.
    - ``holding_period_pnl_histogram`` — buckets correlating holding
      period to per-trade PnL.
    - ``missed_opportunity_regions`` — timeline windows where signal
      pressure existed but no trade fired.
    """

    model_config = ConfigDict(frozen=True)

    metrics: BacktestMetrics
    trade_stats: TradeStats
    regime_performance: list[RegimePerformance]
    signal_misfires: list[SignalMisfire]
    exec_log_summary: dict[str, int]
    exit_reason_histogram: dict[str, int] = Field(default_factory=dict)
    drawdown_trajectory: DrawdownTrajectory | None = None
    holding_period_pnl_histogram: list[HoldingPeriodBucket] = Field(default_factory=list)
    missed_opportunity_regions: list[MissedOpportunityRegion] = Field(default_factory=list)


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


def _exit_reason_histogram(trades: list[Trade]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for trade in trades:
        reason = trade.reason_out or "unspecified"
        counts[reason] += 1
    return dict(sorted(counts.items()))


_DRAWDOWN_THRESHOLDS = (
    ("flat", 0.005),
    ("shallow", 0.05),
    ("moderate", 0.15),
)


def _drawdown_trajectory(equity: list[EquityPoint]) -> DrawdownTrajectory | None:
    """Walk the equity series, identify drawdown episodes, classify shape.

    A drawdown episode begins when drawdown crosses below zero and ends
    when equity recovers to the prior peak (or the series ends). The
    coarse shape label maps the maximum observed depth to one of the
    bands in :data:`_DRAWDOWN_THRESHOLDS`; deeper than the deepest band
    is labeled ``"deep"``.
    """
    if not equity:
        return None

    episodes: list[DrawdownEpisode] = []
    in_dd = False
    dd_start_idx = 0
    dd_trough_idx = 0
    dd_trough_value = 0.0
    for i, point in enumerate(equity):
        if point.drawdown >= 0.0:
            if in_dd:
                episodes.append(
                    DrawdownEpisode(
                        trough_ts=equity[dd_trough_idx].ts,
                        depth=dd_trough_value,
                        duration_bars=i - dd_start_idx,
                        recovered=True,
                    )
                )
                in_dd = False
            continue
        if not in_dd:
            in_dd = True
            dd_start_idx = i
            dd_trough_idx = i
            dd_trough_value = point.drawdown
        elif point.drawdown < dd_trough_value:
            dd_trough_idx = i
            dd_trough_value = point.drawdown

    if in_dd:
        episodes.append(
            DrawdownEpisode(
                trough_ts=equity[dd_trough_idx].ts,
                depth=dd_trough_value,
                duration_bars=len(equity) - dd_start_idx,
                recovered=False,
            )
        )

    max_depth = min((e.depth for e in episodes), default=0.0)
    longest_duration = max((e.duration_bars for e in episodes), default=0)
    abs_depth = abs(max_depth)
    shape = "deep"
    for label, threshold in _DRAWDOWN_THRESHOLDS:
        if abs_depth <= threshold:
            shape = label
            break
    top_episodes = sorted(episodes, key=lambda e: e.depth)[:3]
    return DrawdownTrajectory(
        shape=shape,
        max_depth=max_depth,
        n_episodes=len(episodes),
        longest_duration_bars=longest_duration,
        episodes=top_episodes,
    )


_HOLDING_BUCKETS_SECONDS = (
    ("0-15m", 15 * 60),
    ("15m-1h", 60 * 60),
    ("1-4h", 4 * 60 * 60),
    ("4-24h", 24 * 60 * 60),
    (">1d", float("inf")),
)


def _holding_period_pnl_histogram(trades: list[Trade]) -> list[HoldingPeriodBucket]:
    if not trades:
        return []
    buckets: dict[str, list[float]] = {label: [] for label, _ in _HOLDING_BUCKETS_SECONDS}
    for trade in trades:
        duration = (trade.exit_ts - trade.entry_ts).total_seconds()
        for label, ceiling in _HOLDING_BUCKETS_SECONDS:
            if duration <= ceiling:
                buckets[label].append(trade.pnl)
                break
    out: list[HoldingPeriodBucket] = []
    for label, _ in _HOLDING_BUCKETS_SECONDS:
        bucket_pnls = buckets[label]
        if not bucket_pnls:
            continue
        out.append(
            HoldingPeriodBucket(
                bucket=label,
                n_trades=len(bucket_pnls),
                avg_pnl=sum(bucket_pnls) / len(bucket_pnls),
                total_pnl=sum(bucket_pnls),
            )
        )
    return out


def _missed_opportunity_regions(
    signals: list[SignalEvent],
    trades: list[Trade],
    *,
    window_seconds: float = 60 * 60,
    min_density: float = 0.5,
) -> list[MissedOpportunityRegion]:
    """Tile the signal timeline into ``window_seconds`` buckets, flag the
    ones where signal pressure exceeded the density floor but no trades
    fired."""
    if not signals:
        return []
    by_ts = sorted(signals, key=lambda s: s.ts)
    start = by_ts[0].ts
    out: list[MissedOpportunityRegion] = []
    # Use timestamp-relative bucketing so the function does not depend
    # on an external bar grid.
    buckets: dict[int, list[SignalEvent]] = defaultdict(list)
    for s in by_ts:
        idx = int((s.ts - start).total_seconds() // window_seconds)
        buckets[idx].append(s)
    for idx in sorted(buckets):
        events = buckets[idx]
        if not events:
            continue
        suppressed = sum(1 for e in events if not e.fired and e.suppressed_by)
        fired_total = sum(1 for e in events if e.fired)
        if not fired_total:
            continue
        density = suppressed / max(1, fired_total + suppressed)
        if density < min_density:
            continue
        window_start = events[0].ts
        window_end = events[-1].ts
        n_trades = sum(1 for t in trades if window_start <= t.entry_ts <= window_end)
        fired_no_trade = max(0, fired_total - n_trades)
        if n_trades == 0 and fired_no_trade == 0 and suppressed == 0:
            continue
        out.append(
            MissedOpportunityRegion(
                start=window_start,
                end=window_end,
                suppression_density=density,
                fired_no_trade=fired_no_trade,
                n_trades_in_region=n_trades,
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
        exit_reason_histogram=_exit_reason_histogram(result.trades),
        drawdown_trajectory=_drawdown_trajectory(result.equity),
        holding_period_pnl_histogram=_holding_period_pnl_histogram(result.trades),
        missed_opportunity_regions=_missed_opportunity_regions(result.signals, result.trades),
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
    "DrawdownEpisode",
    "DrawdownTrajectory",
    "HoldingPeriodBucket",
    "MissedOpportunityRegion",
    "RegimePerformance",
    "SignalMisfire",
    "TradeStats",
    "diagnose",
    "diagnose_node",
]
