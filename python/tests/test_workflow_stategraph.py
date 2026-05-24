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


def test_emit_with_repair_forwards_feedback_and_prev_emission_to_retry() -> None:
    """Stage retries MUST receive validator feedback + previous emission.

    Without this, the LLM repeats the same broken output. The retry
    user prompt must contain both the validator's error message and
    the verbatim previous attempt.
    """
    from strategy_gpt.prompts import StagePrompt  # noqa: PLC0415
    from strategy_gpt.repair import (  # noqa: PLC0415
        RepairConfig,
        ValidationOutcome,
    )
    from strategy_gpt.workflow import _emit_with_repair  # noqa: PLC0415

    seen_users: list[str] = []
    responses = iter(["broken attempt 1", "fixed attempt 2"])

    class _RecordingClient:
        def emit_stage(self, *, prompt: Any, stage: int, model: Any, **_: Any) -> str:
            del stage, model
            seen_users.append(prompt.user)
            return next(responses)

    outcomes = iter(
        [
            ValidationOutcome(
                ok=False, kind="reject_build", feedback="error[E0425]: foo not in scope"
            ),
            ValidationOutcome(ok=True, kind="ok", parsed={"files": {}}),
        ]
    )

    def validate(_text: str) -> ValidationOutcome:
        return next(outcomes)

    parsed, response, kind, feedback = _emit_with_repair(
        stage=3,
        build_prompt=lambda: StagePrompt(system="sys", user="initial user payload"),
        validate=validate,
        client=_RecordingClient(),
        model=_model(),
        repair_config=RepairConfig(k_repair=2),
    )
    assert kind is RejectKind.OK
    assert response == "fixed attempt 2"
    assert parsed == {"files": {}}
    assert feedback == ""
    assert seen_users[0] == "initial user payload"
    # The retry payload must carry both the validator's error and the
    # broken previous emission so the LLM can patch in place.
    retry_user = seen_users[1]
    assert "initial user payload" in retry_user
    assert "E0425" in retry_user
    assert "broken attempt 1" in retry_user
    assert "PREVIOUS_EMISSION" in retry_user


def test_emit_with_repair_fires_progress_sink_around_emit_and_validate() -> None:
    """Operator must see a heartbeat around each LLM call and each
    validate (which for stage 3 runs cargo build and may take minutes).
    """
    from strategy_gpt.prompts import StagePrompt  # noqa: PLC0415
    from strategy_gpt.repair import RepairConfig, ValidationOutcome  # noqa: PLC0415
    from strategy_gpt.workflow import _emit_with_repair  # noqa: PLC0415

    events: list[str] = []

    class _Client:
        def emit_stage(self, *, prompt: Any, stage: int, model: Any, **_: Any) -> str:
            del prompt, stage, model
            return "emitted"

    def validate(_text: str) -> ValidationOutcome:
        return ValidationOutcome(ok=True, parsed={"files": {}})

    _emit_with_repair(
        stage=3,
        build_prompt=lambda: StagePrompt(system="sys", user="usr"),
        validate=validate,
        client=_Client(),
        model=_model(),
        repair_config=RepairConfig(k_repair=0),
        progress_sink=events.append,
    )
    # Expect at least: request → response → compile-start → compile-done.
    assert any("requesting LLM" in e for e in events)
    assert any("LLM emission received" in e for e in events)
    assert any("compiling + linting emission" in e for e in events)
    assert any("compiling + linting done" in e for e in events)


def test_emit_with_repair_surfaces_last_feedback_on_exhaustion() -> None:
    """When repair exhausts, the validator's final feedback (rustc error,
    lint reason, etc.) MUST be returned so the orchestrator can stamp it
    onto ``candidate_reject_rationale`` instead of a generic placeholder.
    """
    from strategy_gpt.prompts import StagePrompt  # noqa: PLC0415
    from strategy_gpt.repair import RepairConfig, ValidationOutcome  # noqa: PLC0415
    from strategy_gpt.workflow import _emit_with_repair  # noqa: PLC0415

    class _AlwaysBrokenClient:
        def emit_stage(self, *, prompt: Any, stage: int, model: Any, **_: Any) -> str:
            del prompt, stage, model
            return "broken"

    def validate(_text: str) -> ValidationOutcome:
        return ValidationOutcome(
            ok=False,
            kind="reject_build",
            feedback="error[E0425]: cannot find function `frob` in this scope",
        )

    parsed, response, kind, feedback = _emit_with_repair(
        stage=3,
        build_prompt=lambda: StagePrompt(system="sys", user="initial"),
        validate=validate,
        client=_AlwaysBrokenClient(),
        model=_model(),
        repair_config=RepairConfig(k_repair=1),
    )
    assert parsed is None
    assert response == "broken"
    assert kind is RejectKind.REJECT_BUILD
    assert "E0425" in feedback
    assert "frob" in feedback


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
