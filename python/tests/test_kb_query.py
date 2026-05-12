"""Tests for the hypothesis-loop ``kb_query`` node."""

from __future__ import annotations

from dataclasses import dataclass

from strategy_gpt.diagnose import (
    Diagnosis,
    RegimePerformance,
    SignalMisfire,
    TradeStats,
)
from strategy_gpt.hypothesis_loop import HypothesisLoopState, KbCitation
from strategy_gpt.kb_query import kb_query_node
from strategy_gpt.types import BacktestMetrics


@dataclass
class _StubProvenance:
    source_id: str
    title: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    page: int | None = None


@dataclass
class _StubItem:
    chunk_id: str
    text: str
    score: float
    provenance: _StubProvenance


@dataclass
class _StubResult:
    items: list[_StubItem]


class _StubKb:
    def __init__(self, items: list[_StubItem]) -> None:
        self._items = items
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, k: int) -> _StubResult:
        self.calls.append((query, k))
        return _StubResult(items=self._items[:k])


def _make_diagnosis() -> Diagnosis:
    return Diagnosis(
        metrics=BacktestMetrics(
            sharpe=0.0,
            sortino=0.0,
            profit_factor=0.0,
            win_ratio=0.0,
            max_drawdown=0.0,
            annualized_return=0.0,
            n_trades=0,
            avg_trade_length_bars=0.0,
        ),
        trade_stats=TradeStats(
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
        ),
        regime_performance=[
            RegimePerformance(
                label="high_vol",
                n_trades=2,
                total_pnl=-1.0,
                win_rate=0.0,
                avg_pnl=-0.5,
                coverage_bars=5,
            ),
            RegimePerformance(
                label="downtrend",
                n_trades=1,
                total_pnl=-0.5,
                win_rate=0.0,
                avg_pnl=-0.5,
                coverage_bars=3,
            ),
        ],
        signal_misfires=[
            SignalMisfire(
                signal="rsi_oversold",
                fired_count=4,
                suppressed_count=1,
                used_count=2,
                fired_no_trade_count=2,
                suppression_reasons={},
            )
        ],
        exec_log_summary={"entry_skipped": 2},
    )


def test_kb_query_attaches_citations() -> None:
    client = _StubKb(
        items=[
            _StubItem(
                chunk_id="c1",
                text="vix backwardation drives vxx decay",
                score=0.9,
                provenance=_StubProvenance(
                    source_id="hull-2018",
                    title="Options",
                    section="Chapter 4",
                    page=42,
                ),
            )
        ]
    )
    state = HypothesisLoopState(diagnosis=_make_diagnosis())
    new = kb_query_node(state, client=client, k=3)
    assert len(new.kb_cites) == 1
    cite = new.kb_cites[0]
    assert cite.source == "hull-2018"
    assert "Chapter 4" in cite.locator
    assert "p.42" in cite.locator
    assert cite.excerpt == "vix backwardation drives vxx decay"


def test_kb_query_uses_diagnosis_derived_query_when_none_supplied() -> None:
    client = _StubKb(items=[])
    state = HypothesisLoopState(diagnosis=_make_diagnosis())
    kb_query_node(state, client=client, k=5)
    assert len(client.calls) == 1
    query, k = client.calls[0]
    # Regime labels + signal names sorted for determinism.
    assert "downtrend" in query
    assert "high_vol" in query
    assert "rsi_oversold" in query
    assert k == 5


def test_kb_query_returns_unchanged_when_no_query_and_no_diagnosis() -> None:
    client = _StubKb(items=[])
    state = HypothesisLoopState()
    new = kb_query_node(state, client=client)
    assert new is state or new.kb_cites == []
    assert client.calls == []


def test_kb_query_appends_to_existing_cites() -> None:
    client = _StubKb(
        items=[
            _StubItem(
                chunk_id="c1",
                text="t1",
                score=0.5,
                provenance=_StubProvenance(source_id="s1", title="S1"),
            )
        ]
    )
    state = HypothesisLoopState(
        diagnosis=_make_diagnosis(),
        kb_cites=[KbCitation(source="prior", locator="p.1")],
    )
    new = kb_query_node(state, client=client, query="explicit query", k=1)
    assert len(new.kb_cites) == 2
    assert new.kb_cites[0].source == "prior"
    assert new.kb_cites[1].source == "s1"
    assert client.calls == [("explicit query", 1)]
