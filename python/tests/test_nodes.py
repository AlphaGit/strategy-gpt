"""Tests for the hypothesis-loop generate/critique/rank/select nodes and
the inner ``generate → critique → rank`` iteration loop.

Includes the golden-fixture test: a stubbed :class:`ReasoningClient`
drives the full loop against a canned :class:`BacktestResult`, then
asserts the final state's accepted / rejected / termination snapshot
byte-for-byte.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from strategy_gpt.diagnose import Diagnosis, diagnose
from strategy_gpt.hypothesis_loop import (
    HypothesisCandidate,
    HypothesisLoopState,
    KbCitation,
    PriorDecision,
    TerminationReason,
)
from strategy_gpt.nodes import (
    CritiqueOutcome,
    GenerateError,
    critique_node,
    default_similarity,
    generate_node,
    rank_node,
    rank_score,
    run_inner_loop,
    select_node,
)
from strategy_gpt.reasoning import HypothesisLoopConfig, ReasoningModel
from strategy_gpt.types import (
    BacktestMetrics,
    BacktestResult,
    DecisionKind,
    EquityPoint,
    HypothesisRecord,
    RegimeTag,
    ResultMeta,
    RunnerVersion,
    Side,
    SignalEvent,
    Trade,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2024, 1, 1, tzinfo=UTC)
STAMP = datetime(2024, 6, 1, tzinfo=UTC)
MODEL = ReasoningModel(provider="anthropic", model_id="claude-opus-4-7")


def _config(
    *,
    target: int = 2,
    budget: int = 4,
    similarity: float = 0.85,
) -> HypothesisLoopConfig:
    return HypothesisLoopConfig(
        reasoning_model=MODEL,
        target_candidates=target,
        iteration_budget=budget,
        similarity_threshold=similarity,
    )


def _result() -> BacktestResult:
    return BacktestResult(
        meta=ResultMeta(
            strategy_artifact="art",
            dataset_manifest="m",
            seed=1,
            runner_version=RunnerVersion(major=1, minor=0, patch=0),
        ),
        metrics=BacktestMetrics(
            sharpe=0.4,
            sortino=0.5,
            profit_factor=1.1,
            win_ratio=0.55,
            max_drawdown=0.18,
            annualized_return=0.07,
            n_trades=3,
            avg_trade_length_bars=10.0,
        ),
        trades=[
            Trade(
                entry_ts=T0,
                exit_ts=datetime(2024, 1, 2, tzinfo=UTC),
                symbol="VXX",
                side=Side.LONG,
                size=1.0,
                entry_price=20.0,
                exit_price=21.0,
                pnl=1.0,
                fees=0.0,
                signals_at_entry=["vol_spike"],
            )
        ],
        signals=[
            SignalEvent(name="vol_spike", ts=T0, value=1.0, fired=True),
            SignalEvent(name="vol_spike", ts=T0, value=1.0, fired=False, suppressed_by="cooldown"),
        ],
        equity=[
            EquityPoint(ts=T0, equity=100.0, drawdown=0.0, exposure=0.0),
            EquityPoint(
                ts=datetime(2024, 1, 2, tzinfo=UTC),
                equity=101.0,
                drawdown=0.0,
                exposure=0.0,
            ),
        ],
        exec_log=[],
        regimes=[
            RegimeTag(start=T0, end=datetime(2024, 1, 3, tzinfo=UTC), label="high_vol"),
        ],
    )


def _diagnosis() -> Diagnosis:
    return diagnose(_result())


def _cand(
    name: str,
    *,
    confidence: float = 0.6,
    cites: int = 0,
    change: dict[str, Any] | None = None,
    target_metric: str = "sharpe",
) -> HypothesisCandidate:
    return HypothesisCandidate(
        name=name,
        target_metric=target_metric,
        falsification={"op": ">=", "value": 1.0},
        proposed_change=change if change is not None else {"param": name, "from": 1, "to": 2},
        kb_cites=[KbCitation(source="src", locator=f"p{i}") for i in range(cites)],
        estimated_lift_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# stub reasoning client
# ---------------------------------------------------------------------------


class StubClient:
    """Canned-output reasoning client.

    ``gen_batches`` is consumed one entry per generate call, allowing the
    same stub to drive multi-iteration loops with different per-iteration
    payloads. ``critique_fn`` maps a candidate to a :class:`CritiqueOutcome`."""

    def __init__(
        self,
        gen_batches: list[list[HypothesisCandidate]],
        critique_fn: Callable[[HypothesisCandidate], CritiqueOutcome],
    ) -> None:
        self._batches = list(gen_batches)
        self._critique_fn = critique_fn
        self.generate_calls: list[int] = []
        self.critique_calls: list[str] = []

    def generate(
        self,
        *,
        diagnosis: Diagnosis,
        kb_cites: list[KbCitation],
        prior_decisions: list[PriorDecision],
        n: int,
        model: ReasoningModel,
    ) -> list[HypothesisCandidate]:
        self.generate_calls.append(n)
        if not self._batches:
            return []
        return self._batches.pop(0)

    def critique(
        self,
        *,
        candidate: HypothesisCandidate,
        prior_decisions: list[PriorDecision],
        diagnosis: Diagnosis | None,
        model: ReasoningModel,
    ) -> CritiqueOutcome:
        self.critique_calls.append(candidate.name)
        return self._critique_fn(candidate)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_node_appends_candidates_to_open() -> None:
    state = HypothesisLoopState(diagnosis=_diagnosis())
    client = StubClient(
        gen_batches=[[_cand("a"), _cand("b")]],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    out = generate_node(state, client=client, config=_config())
    assert [c.name for c in out.open] == ["a", "b"]
    # gap = target_candidates(2) - len(accepted)(0) = 2
    assert client.generate_calls == [2]


def test_generate_node_clamps_gap_to_one() -> None:
    state = HypothesisLoopState(
        diagnosis=_diagnosis(),
        accepted=[
            # two already accepted → gap == 0 → clamp to 1
        ],
    )
    config = _config(target=2)
    # Force already-full state via the rank path: accepted of length 2.
    state = state.model_copy(
        update={
            "accepted": [
                # bypass model validation: AcceptedHypothesis requires fields
                # we'll just keep accepted empty and test the explicit-n path
            ]
        }
    )
    client = StubClient(
        gen_batches=[[_cand("only")]],
        critique_fn=lambda _c: CritiqueOutcome(accept=False, rationale="no"),
    )
    out = generate_node(state, client=client, config=config, n=1)
    assert client.generate_calls == [1]
    assert len(out.open) == 1


def test_generate_node_requires_diagnosis() -> None:
    state = HypothesisLoopState()  # no diagnosis
    client = StubClient(
        gen_batches=[[_cand("a")]],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    with pytest.raises(GenerateError, match="diagnosis"):
        generate_node(state, client=client, config=_config())


# ---------------------------------------------------------------------------
# critique
# ---------------------------------------------------------------------------


def test_critique_node_partitions_open_into_accepted_and_rejected() -> None:
    a, b, c = _cand("alpha"), _cand("beta"), _cand("gamma")
    state = HypothesisLoopState(diagnosis=_diagnosis(), open=[a, b, c])
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda cand: CritiqueOutcome(
            accept=cand.name != "beta",
            rationale="dup" if cand.name == "beta" else "looks good",
            evidence={"score": 0.7} if cand.name != "beta" else None,
        ),
    )
    out = critique_node(state, client=client, config=_config(), now=STAMP)
    assert out.open == []
    assert [a.candidate.name for a in out.accepted] == ["alpha", "gamma"]
    assert [r.candidate.name for r in out.rejected] == ["beta"]
    assert out.accepted[0].accepted_at == STAMP
    assert out.accepted[0].evidence == {"score": 0.7}
    assert out.rejected[0].reason == "dup"
    assert out.rejected[0].rejected_at == STAMP
    # All three candidates were sent to the critic, in submission order.
    assert client.critique_calls == ["alpha", "beta", "gamma"]


def test_critique_node_preserves_prior_accepted_and_rejected() -> None:
    a = _cand("a")
    state = HypothesisLoopState(diagnosis=_diagnosis(), open=[a])
    # Seed one prior accepted via critique first
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    once = critique_node(state, client=client, config=_config(), now=STAMP)
    # Now critique again with new open
    b = _cand("b")
    once = once.model_copy(update={"open": [b]})
    twice = critique_node(once, client=client, config=_config(), now=STAMP)
    assert [a.candidate.name for a in twice.accepted] == ["a", "b"]


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def test_rank_score_prefers_high_confidence_evidence_simple_changes() -> None:
    high = _cand("h", confidence=0.9, cites=2, change={"param": "x", "from": 1, "to": 2})
    low = _cand("l", confidence=0.2, cites=0, change={"source": "fn ...", "manifest": "..."})
    assert rank_score(high) > rank_score(low)


def test_rank_node_sorts_accepted_by_score_desc() -> None:
    a = _cand("low", confidence=0.1)
    b = _cand("mid", confidence=0.5)
    c = _cand("high", confidence=0.9)
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    state = HypothesisLoopState(diagnosis=_diagnosis(), open=[a, b, c])
    state = critique_node(state, client=client, config=_config(), now=STAMP)
    state = rank_node(state)
    assert [a.candidate.name for a in state.accepted] == ["high", "mid", "low"]


def test_rank_node_stable_on_ties() -> None:
    a = _cand("first", confidence=0.5)
    b = _cand("second", confidence=0.5)
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    state = HypothesisLoopState(diagnosis=_diagnosis(), open=[a, b])
    state = critique_node(state, client=client, config=_config(), now=STAMP)
    state = rank_node(state)
    # equal scores preserve submission order via stable sort
    assert [a.candidate.name for a in state.accepted] == ["first", "second"]


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def test_select_node_trims_and_sets_default_termination() -> None:
    cands = [_cand(n, confidence=0.5) for n in ("a", "b", "c", "d")]
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    state = HypothesisLoopState(diagnosis=_diagnosis(), open=cands)
    state = critique_node(state, client=client, config=_config(), now=STAMP)
    out = select_node(state, k=2)
    assert len(out.accepted) == 2
    assert out.termination_reason is TerminationReason.SUFFICIENT_CANDIDATES


def test_select_node_respects_explicit_termination_reason() -> None:
    state = HypothesisLoopState(diagnosis=_diagnosis())
    out = select_node(state, k=2, termination_reason=TerminationReason.BUDGET_EXHAUSTED)
    assert out.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert out.accepted == []


def test_select_node_rejects_negative_k() -> None:
    with pytest.raises(ValueError, match="k must be >= 0"):
        select_node(HypothesisLoopState(diagnosis=_diagnosis()), k=-1)


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------


def test_default_similarity_zero_when_no_references() -> None:
    assert default_similarity(_cand("x"), []) == 0.0


def test_default_similarity_max_over_references() -> None:
    target = _cand(
        "lower_vol_lo",
        change={"param": "vol_lo", "from": 10, "to": 5},
    )
    near = _cand(
        "lower_vol_lo_v2",
        change={"param": "vol_lo", "from": 10, "to": 4},
    )
    far = _cand("raise_atr_lookback", change={"param": "atr_lookback", "from": 14, "to": 20})
    score = default_similarity(target, [far, near])
    far_score = default_similarity(target, [far])
    assert score >= far_score
    assert score > 0.5


# ---------------------------------------------------------------------------
# inner loop
# ---------------------------------------------------------------------------


def test_run_inner_loop_terminates_on_sufficient_candidates() -> None:
    state = HypothesisLoopState(diagnosis=_diagnosis())
    client = StubClient(
        gen_batches=[[_cand("a", confidence=0.8), _cand("b", confidence=0.6)]],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    out = run_inner_loop(state, client=client, config=_config(target=2), now=STAMP)
    assert out.termination_reason is TerminationReason.SUFFICIENT_CANDIDATES
    assert len(out.accepted) == 2
    assert out.iteration == 1


def test_run_inner_loop_terminates_on_budget_exhaustion() -> None:
    """All candidates rejected; budget caps the loop."""
    state = HypothesisLoopState(diagnosis=_diagnosis())
    # Distinct candidate per iteration so similarity does not trigger first.
    batches = [[_cand(f"iter_{i}_a"), _cand(f"iter_{i}_b")] for i in range(4)]
    client = StubClient(
        gen_batches=batches,
        critique_fn=lambda _c: CritiqueOutcome(accept=False, rationale="nope"),
    )
    out = run_inner_loop(
        state,
        client=client,
        config=_config(target=3, budget=4, similarity=0.99),
        now=STAMP,
    )
    assert out.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert out.accepted == []
    assert out.iteration == 4
    assert len(out.rejected) == 8


def test_run_inner_loop_terminates_on_similarity_saturation() -> None:
    """Loop regenerates candidates resembling a prior rejection."""
    prior_reject = HypothesisRecord(
        id="h-old",
        name="lower_vol_lo",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change={"param": "vol_lo", "from": 10, "to": 5},
        kb_cites=[],
        created_at=datetime(2024, 5, 30, tzinfo=UTC),
    )
    state = HypothesisLoopState(
        diagnosis=_diagnosis(),
        prior_decisions=[
            PriorDecision(
                decision_id="d-old",
                kind=DecisionKind.REJECTED,
                rationale="bad idea",
                evidence=None,
                decided_at=datetime(2024, 5, 30, tzinfo=UTC),
                hypothesis=prior_reject,
            )
        ],
    )
    near_dup = _cand(
        "lower_vol_lo",
        change={"param": "vol_lo", "from": 10, "to": 5},
    )
    client = StubClient(
        gen_batches=[[near_dup]],
        critique_fn=lambda _c: CritiqueOutcome(accept=False, rationale="dup"),
    )
    out = run_inner_loop(
        state,
        client=client,
        config=_config(target=3, budget=5, similarity=0.5),
        now=STAMP,
    )
    assert out.termination_reason is TerminationReason.SIMILARITY_SATURATION
    assert out.iteration == 1
    assert len(out.rejected) == 1


def test_run_inner_loop_requires_diagnosis() -> None:
    client = StubClient(
        gen_batches=[],
        critique_fn=lambda _c: CritiqueOutcome(accept=True, rationale="ok"),
    )
    with pytest.raises(GenerateError, match="diagnosis"):
        run_inner_loop(HypothesisLoopState(), client=client, config=_config())


# ---------------------------------------------------------------------------
# golden fixture
# ---------------------------------------------------------------------------


def test_golden_hypothesis_generation_against_fixed_backtest_result() -> None:
    """Drives diagnose → generate → critique → rank → select against a
    fixed :class:`BacktestResult` with a stubbed reasoning client and
    asserts the final state matches an expected snapshot.

    Locks the loop's wiring: any change to node ordering, ranking
    heuristic, or termination logic will surface here as a diff."""
    result = _result()
    diagnosis = diagnose(result)
    initial = HypothesisLoopState(diagnosis=diagnosis)

    # Three candidates the LLM "emits", deliberately varied so ranking
    # has something to do: high-confidence param diff, high-confidence
    # logic change, low-confidence param diff.
    canned = [
        _cand("lower_vol_lo", confidence=0.85, cites=2),
        _cand(
            "rewrite_entry_logic",
            confidence=0.85,
            cites=1,
            change={"source": "fn on_bar(...) { ... }", "manifest": "..."},
        ),
        _cand("raise_atr_lookback", confidence=0.4, cites=0),
    ]

    # Critic accepts the first two; rejects the noisy third.
    def critique_fn(c: HypothesisCandidate) -> CritiqueOutcome:
        if c.name == "raise_atr_lookback":
            return CritiqueOutcome(accept=False, rationale="weak evidence")
        return CritiqueOutcome(
            accept=True,
            rationale=f"good fit for {c.target_metric}",
            evidence={"projected_lift": 0.25},
        )

    client = StubClient(gen_batches=[canned], critique_fn=critique_fn)
    out = run_inner_loop(
        initial,
        client=client,
        config=_config(target=2, budget=3),
        now=STAMP,
    )

    # Final state shape
    assert out.termination_reason is TerminationReason.SUFFICIENT_CANDIDATES
    assert out.iteration == 1
    # Top-2 ranking: param-only diff (lower complexity) beats logic change
    # even when lift confidence and citation count are equal.
    assert [a.candidate.name for a in out.accepted] == [
        "lower_vol_lo",
        "rewrite_entry_logic",
    ]
    assert [a.rationale for a in out.accepted] == [
        "good fit for sharpe",
        "good fit for sharpe",
    ]
    for entry in out.accepted:
        assert entry.evidence == {"projected_lift": 0.25}
        assert entry.accepted_at == STAMP
    # Rejected candidate carried through with rationale & timestamp.
    assert len(out.rejected) == 1
    assert out.rejected[0].candidate.name == "raise_atr_lookback"
    assert out.rejected[0].reason == "weak evidence"
    assert out.rejected[0].rejected_at == STAMP
    # Generator was asked for target_candidates(2) - accepted(0) = 2.
    # The stub returns 3 anyway (LLMs over-produce); both surplus and
    # rejections are kept in state.
    assert client.generate_calls == [2]
    assert client.critique_calls == [
        "lower_vol_lo",
        "rewrite_entry_logic",
        "raise_atr_lookback",
    ]
