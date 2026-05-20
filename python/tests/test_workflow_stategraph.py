"""Validate LangGraph wiring of the hypothesis loop.

Exercises (a) that ``compile_workflow`` produces a real CompiledGraph
with the expected node + edge topology, (b) that ``should_continue``
honors the termination priority, and (c) that ``mini_optimize_step``
honors a ``max_backtests`` ceiling at iteration start.

End-to-end graph invocation is deferred to the smoke test
(:mod:`tests/test_smoke`); these tests verify the wiring contract.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from strategy_gpt.hypothesis_loop import (
    AcceptedHypothesis,
    HypothesisCandidate,
    TerminationReason,
)
from strategy_gpt.markdown_io import Stage1Idea, Stage2Commitments
from strategy_gpt.reasoning import HypothesisLoopConfig, ReasoningModel
from strategy_gpt.reject_taxonomy import RejectKind
from strategy_gpt.verdict_critique import DeterministicVerdictCritic
from strategy_gpt.workflow import (
    NodeClients,
    compile_workflow,
    mini_optimize_step,
    should_continue,
)


def _model() -> ReasoningModel:
    return ReasoningModel(provider="anthropic", model_id="claude-opus-4-7")


def _config(**overrides: Any) -> HypothesisLoopConfig:
    base = HypothesisLoopConfig(
        reasoning_model=_model(),
        iteration_budget=4,
        target_candidates=3,
    )
    if not overrides:
        return base
    return dataclasses.replace(base, **overrides)


class _StubKb:
    def retrieve(self, query: str, k: int) -> Any:
        _ = (query, k)

        class _Result:
            items: list[Any] = []  # noqa: RUF012 — fixture class, not a config object

        return _Result()


class _StubStageClient:
    def emit_stage(self, *, prompt: Any, stage: int, model: Any, **_: Any) -> str:
        return ""


class _StubBuildPipeline:
    def lint(self, source: str, manifest: Any) -> Any:
        raise NotImplementedError

    def build(self, source: str, manifest: Any) -> Any:
        raise NotImplementedError


def _evaluate_fold(_params: Mapping[str, Any], _fold_idx: int) -> Any:
    raise NotImplementedError


def _clients() -> NodeClients:
    return NodeClients(
        kb=_StubKb(),
        stage_client=_StubStageClient(),
        build_pipeline=_StubBuildPipeline(),
        evaluate_fold=_evaluate_fold,
        verdict_critic=DeterministicVerdictCritic(),
        prompt_api="(test)",
        allowed_metrics=["sharpe"],
        baseline_files={"src/lib.rs": "fn x(){}", "Cargo.toml": ""},
        baseline_params_schema=None,
        baseline_per_fold_scores=[1.0, 1.0, 1.0],
        baseline_metrics={"max_drawdown": 0.08},
        baseline_aggregate_score=1.0,
        objective_metric="sharpe",
        kept_bounds={},
    )


def test_compile_workflow_returns_compiled_state_graph() -> None:
    graph = compile_workflow(_clients())
    assert isinstance(graph, CompiledStateGraph)


def test_workflow_node_set_matches_spec() -> None:
    graph = compile_workflow(_clients())
    nodes = set(graph.get_graph().nodes)
    expected = {
        "diagnose",
        "kb_query",
        "kb_filter",
        "generate_stage1_idea",
        "cheap_critique",
        "generate_stage2_commitments",
        "generate_stage3_files",
        "mini_optimize",
        "mechanical_gate",
        "verdict_critique",
        "rank",
        "select",
    }
    # langgraph adds implicit __start__ / __end__ nodes; only check superset
    assert expected.issubset(nodes)


def test_should_continue_routes_to_select_when_budget_exhausted() -> None:
    state: dict[str, Any] = {
        "config": _config(iteration_budget=2),
        "accepted": [],
        "iteration": 2,
    }
    assert should_continue(state) == "select"  # type: ignore[arg-type]
    assert state["termination_reason"] is TerminationReason.BUDGET_EXHAUSTED


def test_should_continue_routes_to_select_when_target_met() -> None:
    candidate = HypothesisCandidate(
        name="c",
        target_metric="sharpe",
        falsification={},
        proposed_change={},
        kb_cites=[],
        estimated_lift_confidence=0.5,
    )
    now = datetime.now(UTC)
    accepted = [
        AcceptedHypothesis(candidate=candidate, rationale="ok", evidence=None, accepted_at=now)
        for _ in range(3)
    ]
    state: dict[str, Any] = {
        "config": _config(target_candidates=3, iteration_budget=10),
        "accepted": accepted,
        "iteration": 1,
    }
    assert should_continue(state) == "select"  # type: ignore[arg-type]
    assert state["termination_reason"] is TerminationReason.SUFFICIENT_CANDIDATES


def test_should_continue_routes_back_when_budget_remaining() -> None:
    state: dict[str, Any] = {
        "config": _config(iteration_budget=4, target_candidates=3),
        "accepted": [],
        "iteration": 1,
    }
    assert should_continue(state) == "continue"  # type: ignore[arg-type]


def test_should_continue_respects_max_backtests() -> None:
    state: dict[str, Any] = {
        "config": _config(iteration_budget=10, target_candidates=10),
        "accepted": [],
        "iteration": 1,
        "backtests_consumed": 200,
        "max_backtests": 150,
    }
    assert should_continue(state) == "select"  # type: ignore[arg-type]
    assert state["termination_reason"] is TerminationReason.BUDGET_EXHAUSTED


def test_mini_optimize_rejects_when_budget_would_exceed() -> None:
    clients = _clients()
    stage2 = Stage2Commitments(
        falsification={
            "primary": {"metric": "sharpe", "direction": "gt", "delta_vs_baseline": 0.1}
        },
        param_intent={"added": [], "kept": [], "removed": []},
    )
    state: dict[str, Any] = {
        "stage1_idea": Stage1Idea(
            candidate_name="c",
            rationale="r",
            expected_lift_confidence=0.5,
            expected_side_effects=[],
        ),
        "stage2_parsed": stage2,
        "config": _config(),
        "backtests_consumed": 100,
        "max_backtests": 110,
    }
    update = mini_optimize_step(state, clients)  # type: ignore[arg-type]
    assert update["candidate_reject_kind"] is RejectKind.REJECT_NOISE
    assert "budget remaining" in update["candidate_reject_rationale"]
