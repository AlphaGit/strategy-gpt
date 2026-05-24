"""Mechanical-failure (deferred) persistence + prior-decision filtering.

Spec: ``hypothesis-loop::mechanical-failures-are-deferred-not-rejected``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from strategy_gpt.hypothesis_loop import HypothesisCandidate, RejectedHypothesis
from strategy_gpt.hypothesize import _persist_candidate, _project_prior_decisions
from strategy_gpt.per_strategy_ledger import PerStrategyLedger
from strategy_gpt.types import DecisionKind
from strategy_gpt.workflow import HypothesizeState


def _make_state(*, candidate_name: str = "time_based_exit_cap") -> HypothesizeState:
    """Build a minimal `HypothesizeState` adequate for `_persist_candidate`."""
    from strategy_gpt.markdown_io import Stage1Idea, Stage2Commitments, Stage3Files  # noqa: PLC0415

    snap: HypothesizeState = {  # type: ignore[typeddict-item]
        "strategy": "demo",
        "stage1_idea": Stage1Idea(
            candidate_name=candidate_name,
            rationale="add exit_after_bars to force closes",
            expected_lift_confidence=0.32,
            expected_side_effects=["Higher turnover"],
        ),
        "stage2_parsed": Stage2Commitments(
            falsification={
                "primary": {
                    "metric": "annualized_return",
                    "direction": "gt",
                    "delta_vs_baseline": 0.01,
                }
            },
            param_intent={"added": [], "kept": [], "removed": []},
        ),
        "stage3_parsed": Stage3Files(files={}, deleted=[]),
        "stage1_response": "",
        "stage2_response": "",
        "stage3_response": "",
        "kb_cites": [],
    }
    return snap


def test_persist_candidate_writes_deferred_for_mechanical_failure(tmp_path: Path) -> None:
    led = PerStrategyLedger(tmp_path, "demo")
    candidate = HypothesisCandidate(
        name="time_based_exit_cap",
        target_metric="annualized_return",
        falsification={
            "primary": {
                "metric": "annualized_return",
                "direction": "gt",
                "delta_vs_baseline": 0.01,
            }
        },
        proposed_change={"files_manifest": {}},
        kb_cites=[],
        estimated_lift_confidence=0.32,
    )
    rejected = RejectedHypothesis(
        candidate=candidate,
        reason="error[E0425]: cannot find function `foo`",
        rejected_at=datetime.now(UTC),
        reject_kind="reject_build",
    )

    decision_id = _persist_candidate(
        led,
        strategy="demo",
        state=_make_state(),
        decision_kind=DecisionKind.DEFERRED,
        rationale=rejected.reason,
        evidence={"reject_kind": rejected.reject_kind},
        baseline_files_hash="basehash",
    )
    assert decision_id

    decisions = list(led.decisions_iter())
    assert len(decisions) == 1
    assert decisions[0].outcome.kind == "deferred"
    assert decisions[0].outcome.stage == "reject_build"


def test_project_prior_decisions_skips_deferred(tmp_path: Path) -> None:
    led = PerStrategyLedger(tmp_path, "demo")

    # Persist a deferred + a real rejected candidate.
    for kind, reject in (
        (DecisionKind.DEFERRED, "reject_build"),
        (DecisionKind.REJECTED, "reject_verdict"),
    ):
        _persist_candidate(
            led,
            strategy="demo",
            state=_make_state(candidate_name=f"{kind.value}_idea"),
            decision_kind=kind,
            rationale="rationale",
            evidence={"reject_kind": reject},
            baseline_files_hash="basehash",
        )

    priors = _project_prior_decisions(led, limit=50)
    # Only the rejected one shows up. The deferred candidate's idea is
    # invisible to the duplicate-similarity check, so stage-1 can
    # re-propose it freely.
    assert len(priors) == 1
    assert priors[0].kind is DecisionKind.REJECTED
