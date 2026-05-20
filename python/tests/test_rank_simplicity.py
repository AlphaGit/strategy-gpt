"""Tests for the simplicity-preferring rank score."""

from __future__ import annotations

from strategy_gpt.hypothesis_loop import HypothesisCandidate, KbCitation
from strategy_gpt.nodes import RankWeights, rank_score


def _candidate(*, proposed_change, lift=0.5, n_cites=1):
    return HypothesisCandidate(
        name="c",
        target_metric="sharpe",
        falsification={},
        proposed_change=proposed_change,
        kb_cites=[KbCitation(source="s", locator="1") for _ in range(n_cites)],
        estimated_lift_confidence=lift,
    )


def test_tie_broken_by_simplicity() -> None:
    simpler = _candidate(
        proposed_change={
            "param_intent": {"added": [], "removed": ["a", "b"]},
        }
    )
    bulkier = _candidate(
        proposed_change={
            "param_intent": {"added": [{"name": "a"}, {"name": "b"}], "removed": []},
        }
    )
    assert rank_score(simpler) > rank_score(bulkier)


def test_removals_earn_simplicity_bonus() -> None:
    removed = _candidate(proposed_change={"param_intent": {"added": [], "removed": ["a"]}})
    neutral = _candidate(proposed_change={"param_intent": {"added": [], "removed": []}})
    assert rank_score(removed) > rank_score(neutral)


def test_additions_get_continuous_penalty() -> None:
    one_added = _candidate(
        proposed_change={"param_intent": {"added": [{"name": "x"}], "removed": []}}
    )
    five_added = _candidate(
        proposed_change={
            "param_intent": {
                "added": [{"name": f"x{i}"} for i in range(5)],
                "removed": [],
            }
        }
    )
    assert rank_score(one_added) > rank_score(five_added)


def test_weights_override_changes_ranking() -> None:
    cand = _candidate(proposed_change={"param_intent": {"added": [], "removed": ["a", "b", "c"]}})
    default = rank_score(cand)
    boosted = rank_score(cand, weights=RankWeights(simplicity_bonus=1.0))
    assert boosted > default


def test_empty_proposed_change_falls_back_to_lift_plus_evidence() -> None:
    cand = _candidate(proposed_change={}, lift=0.4, n_cites=2)
    s = rank_score(cand)
    # no penalty, no bonus → just lift*0.55 + evidence*0.25
    expected = 0.55 * 0.4 + 0.25 * min(1.0, 0.4 + 0.2 * 2)
    assert abs(s - expected) < 1e-9
