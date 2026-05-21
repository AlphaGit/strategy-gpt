"""LangGraph StateGraph wiring for the hypothesis loop.

Implements ``hypothesis-loop::langgraph-workflow-with-explicit-nodes``:
the workflow is a real :class:`langgraph.graph.StateGraph` whose nodes
correspond one-to-one with the spec sequence:

    diagnose -> kb_query -> kb_filter -> [inner loop:
        generate_stage1_idea -> cheap_critique ->
        generate_stage2_commitments -> generate_stage3_files ->
        build_and_smoke -> mini_optimize ->
        mechanical_gate -> verdict_critique -> rank ->
        should_continue?
    ] -> select

State transitions are explicit and observable. Each node delegates the
heavy lifting to the pure functions in :mod:`strategy_gpt.diagnose` /
:mod:`strategy_gpt.kb_query` / :mod:`strategy_gpt.cheap_critique` /
:mod:`strategy_gpt.repair` / :mod:`strategy_gpt.tester` /
:mod:`strategy_gpt.mechanical_gate` / :mod:`strategy_gpt.verdict_critique` /
:mod:`strategy_gpt.nodes`; the graph layer is a thin orchestration shell
so tests can exercise the pure functions directly while the graph is
exercised end-to-end by :func:`compile_workflow`.

The state is a :class:`HypothesizeState` TypedDict — LangGraph's
preferred shape — carrying the cumulative loop context plus the
in-flight candidate fields. The dependency bag :class:`NodeClients`
injects every collaborator (KB, reasoning, build pipeline, tester
evaluator, verdict critic) so the graph is fully testable without any
network.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from .build_pipeline import _BuildPipelineLike
from .cheap_critique import CheapCritiqueOutcome, cheap_critique
from .diagnose import Diagnosis, diagnose
from .hypothesis_loop import (
    AcceptedHypothesis,
    HypothesisCandidate,
    KbCitation,
    PriorDecision,
    RejectedHypothesis,
    TerminationReason,
)
from .kb_query import KbClient, kb_filter_node, kb_query_node
from .markdown_io import Stage1Idea, Stage2Commitments, Stage3Files
from .mechanical_gate import (
    MechanicalGateConfig,
    MechanicalGateOutcome,
    mechanical_gate,
)
from .nodes import rank_score
from .per_strategy_ledger import (
    AddedParam,
    Falsification,
    FalsificationPrimary,
    GuardConstraint,
    ParamIntent,
)
from .prompts import (
    build_stage1_prompt,
    build_stage2_prompt,
    build_stage3_prompt,
)
from .reasoning import HypothesisLoopConfig
from .reasoning_clients import StageReasoningClient
from .reject_taxonomy import RejectKind, format_rationale, is_repairable
from .repair import RepairConfig, run_stage_with_repair
from .tester import (
    AttemptWithOptimizeResult,
    EvaluateFoldFn,
    attempt_with_optimize,
)
from .types import BacktestResult
from .validation import validate_stage1, validate_stage2, validate_stage3
from .verdict_critique import (
    VerdictCritiqueClient,
    VerdictCritiqueDecision,
    VerdictCritiqueInput,
    verdict_critique_node,
)

# ---------------------------------------------------------------------------
# Dependency injection bag
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeClients:
    """All collaborators the workflow nodes depend on.

    Bundled so the graph nodes can resolve their dependencies by reading
    the bag rather than receiving a long signature. Tests construct the
    bag with stubs; the orchestrator constructs it with the real
    Anthropic / OpenAI / build-pipeline / engine wiring.
    """

    kb: KbClient
    stage_client: StageReasoningClient
    build_pipeline: _BuildPipelineLike
    evaluate_fold: EvaluateFoldFn
    verdict_critic: VerdictCritiqueClient
    prompt_api: str
    allowed_metrics: list[str]
    baseline_files: Mapping[str, str]
    baseline_params_schema: dict[str, Any] | None
    baseline_per_fold_scores: Sequence[float]
    baseline_metrics: Mapping[str, float]
    baseline_aggregate_score: float
    objective_metric: str
    kept_bounds: Mapping[str, Any]
    repair_config: RepairConfig = field(default_factory=RepairConfig)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class HypothesizeState(TypedDict, total=False):
    """LangGraph state for the hypothesis loop.

    Keys are populated incrementally as the graph executes. The state is
    a TypedDict (not pydantic) because LangGraph's reducer expects plain
    dicts; field reducers (``add_messages``-style) are unnecessary for
    this workflow — every node writes a complete replacement value.
    """

    strategy: str
    baseline_result: BacktestResult
    diagnosis: Diagnosis
    kb_cites: list[KbCitation]
    prior_decisions: list[PriorDecision]
    accepted: list[AcceptedHypothesis]
    rejected: list[RejectedHypothesis]
    intra_run_history: list[HypothesisCandidate]
    iteration: int
    backtests_consumed: int
    termination_reason: TerminationReason
    config: HypothesisLoopConfig
    max_backtests: int | None
    # in-flight candidate slots — cleared between iterations
    stage1_response: str
    stage1_idea: Stage1Idea
    stage2_response: str
    stage2_parsed: Stage2Commitments
    stage3_response: str
    stage3_parsed: Stage3Files
    candidate_reject_kind: RejectKind | None
    candidate_reject_rationale: str
    candidate_attempt_result: AttemptWithOptimizeResult | None
    gate_outcome: MechanicalGateOutcome | None
    verdict_decision: VerdictCritiqueDecision | None


# ---------------------------------------------------------------------------
# Node implementations — thin wrappers around the existing pure functions
# ---------------------------------------------------------------------------


def diagnose_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    diagnosis = diagnose(state["baseline_result"])
    return {"diagnosis": diagnosis}


def kb_query_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    from .hypothesis_loop import HypothesisLoopState  # noqa: PLC0415 — avoid cycle

    legacy = HypothesisLoopState(
        diagnosis=state["diagnosis"],
        kb_cites=list(state.get("kb_cites", [])),
        prior_decisions=list(state.get("prior_decisions", [])),
    )
    updated = kb_query_node(legacy, client=clients.kb)
    return {"kb_cites": list(updated.kb_cites)}


def kb_filter_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    from .hypothesis_loop import HypothesisLoopState  # noqa: PLC0415

    legacy = HypothesisLoopState(
        diagnosis=state["diagnosis"],
        kb_cites=list(state.get("kb_cites", [])),
        prior_decisions=list(state.get("prior_decisions", [])),
    )
    filtered = kb_filter_node(legacy)
    return {"kb_cites": list(filtered.kb_cites)}


def _emit_with_repair(  # noqa: PLR0913 — every dependency is needed at this seam
    *,
    stage: Literal[1, 2, 3],
    build_prompt: Callable[[], Any],
    validate: Callable[[str], Any],
    client: StageReasoningClient,
    model: Any,  # noqa: ANN401 — ReasoningModel value-object; protocol-typed at call site
    repair_config: RepairConfig,
) -> tuple[Any, str, RejectKind]:
    prompt = build_prompt()

    def emit(_feedback: str) -> str:
        return client.emit_stage(prompt=prompt, stage=stage, model=model)

    outcome = run_stage_with_repair(
        stage=stage,
        emit_fn=emit,
        validate_fn=validate,
        config=repair_config,
    )
    if not outcome.accepted:
        try:
            kind = RejectKind(outcome.final_reject_kind)
        except ValueError:
            kind = RejectKind.EXHAUSTED_REPAIR_BUDGET
        return None, outcome.final_response, kind
    return outcome.final_parsed, outcome.final_response, RejectKind.OK


def generate_stage1_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    cfg: HypothesisLoopConfig = state["config"]

    def build_prompt() -> Any:  # noqa: ANN401
        return build_stage1_prompt(
            strategy_name=state["strategy"],
            diagnosis=state["diagnosis"],
            kb_cites=state.get("kb_cites", []),
            prior_decisions=state.get("prior_decisions", []),
            intra_run_history=state.get("intra_run_history", []),
        )

    parsed, response, kind = _emit_with_repair(
        stage=1,
        build_prompt=build_prompt,
        validate=validate_stage1,
        client=clients.stage_client,
        model=cfg.reasoning_model,
        repair_config=clients.repair_config,
    )
    if kind is not RejectKind.OK:
        return {
            "candidate_reject_kind": kind,
            "candidate_reject_rationale": format_rationale(
                section="# Idea", detail="stage-1 emission failed validation"
            ).summary,
            "stage1_response": response,
        }
    return {"stage1_response": response, "stage1_idea": parsed, "candidate_reject_kind": None}


def cheap_critique_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    idea = state["stage1_idea"]
    outcome: CheapCritiqueOutcome = cheap_critique(
        idea,
        prior_decisions=state.get("prior_decisions", []),
        diagnosis=state.get("diagnosis"),
    )
    if outcome.accept:
        return {}
    return {
        "candidate_reject_kind": RejectKind.REJECT_VERDICT,  # idea-level reject
        "candidate_reject_rationale": outcome.rationale,
    }


def generate_stage2_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    cfg: HypothesisLoopConfig = state["config"]

    def build_prompt() -> Any:  # noqa: ANN401
        return build_stage2_prompt(
            strategy_name=state["strategy"],
            stage1_response=state["stage1_response"],
            stage1_parsed=state["stage1_idea"],
            prompt_api=clients.prompt_api,
            baseline_params_schema=clients.baseline_params_schema,
            allowed_metrics=clients.allowed_metrics,
        )

    def validate(text: str) -> Any:  # noqa: ANN401
        return validate_stage2(text, allowed_metrics=frozenset(clients.allowed_metrics) or None)

    parsed, response, kind = _emit_with_repair(
        stage=2,
        build_prompt=build_prompt,
        validate=validate,
        client=clients.stage_client,
        model=cfg.reasoning_model,
        repair_config=clients.repair_config,
    )
    if kind is not RejectKind.OK:
        return {
            "candidate_reject_kind": kind,
            "candidate_reject_rationale": "stage-2 emission failed validation",
            "stage2_response": response,
        }
    return {"stage2_response": response, "stage2_parsed": parsed}


def generate_stage3_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    cfg: HypothesisLoopConfig = state["config"]
    stage2 = state["stage2_parsed"]

    def build_prompt() -> Any:  # noqa: ANN401
        return build_stage3_prompt(
            strategy_name=state["strategy"],
            stage1_response=state["stage1_response"],
            stage2_response=state["stage2_response"],
            stage2_parsed=stage2,
            prompt_api=clients.prompt_api,
            baseline_files=dict(clients.baseline_files),
        )

    def validate(text: str) -> Any:  # noqa: ANN401
        return validate_stage3(
            text,
            pipeline=clients.build_pipeline,
            stage2_param_intent=stage2.param_intent,
        )

    parsed, response, kind = _emit_with_repair(
        stage=3,
        build_prompt=build_prompt,
        validate=validate,
        client=clients.stage_client,
        model=cfg.reasoning_model,
        repair_config=clients.repair_config,
    )
    if kind is not RejectKind.OK:
        return {
            "candidate_reject_kind": kind,
            "candidate_reject_rationale": "stage-3 emission failed validation",
            "stage3_response": response,
        }
    files = parsed["files"] if isinstance(parsed, dict) else parsed
    return {"stage3_response": response, "stage3_parsed": files}


def _build_falsification(stage2: Stage2Commitments) -> Falsification:
    fal = stage2.falsification
    primary_raw = fal["primary"]
    primary = FalsificationPrimary(
        metric=primary_raw["metric"],
        direction=primary_raw["direction"],
        delta_vs_baseline=float(primary_raw["delta_vs_baseline"]),
    )
    guards = [GuardConstraint(**g) for g in fal.get("guard_constraints", [])]
    return Falsification(primary=primary, guard_constraints=guards)


def _build_param_intent(stage2: Stage2Commitments) -> ParamIntent:
    pi = stage2.param_intent
    added = [AddedParam(**a) for a in pi.get("added", [])]
    return ParamIntent(
        added=added,
        kept=list(pi.get("kept", [])),
        removed=list(pi.get("removed", [])),
    )


def mini_optimize_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    cfg: HypothesisLoopConfig = state["config"]
    trials = getattr(cfg, "mini_optimize_trials", 64)
    folds = len(clients.baseline_per_fold_scores)
    consumed = state.get("backtests_consumed", 0)
    max_backtests = state.get("max_backtests")
    cost = trials * folds
    if max_backtests is not None and consumed + cost > max_backtests:
        return {
            "candidate_reject_kind": RejectKind.REJECT_NOISE,
            "candidate_reject_rationale": (
                f"mini-optimize would consume {cost} backtests; "
                f"budget remaining {max_backtests - consumed}"
            ),
        }

    stage2 = state["stage2_parsed"]
    falsification = _build_falsification(stage2)
    param_intent = _build_param_intent(stage2)
    result = attempt_with_optimize(
        strategy_artifact=state["strategy"],
        param_intent=param_intent,
        falsification=falsification,
        folds=folds,
        method="sobol",
        trials=trials,
        kept_bounds=clients.kept_bounds,
        objective_metric=clients.objective_metric,
        evaluate_fold=clients.evaluate_fold,
        baseline_per_fold_scores=clients.baseline_per_fold_scores,
        baseline_metrics=clients.baseline_metrics,
    )
    return {
        "candidate_attempt_result": result,
        "backtests_consumed": consumed + cost,
    }


def mechanical_gate_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    result = state["candidate_attempt_result"]
    if result is None:
        return {}
    cfg: HypothesisLoopConfig = state["config"]
    gate_cfg = MechanicalGateConfig(
        k=getattr(cfg, "borderline_k", 1.0),
        fold_cv_threshold=getattr(cfg, "fold_cv_threshold", 0.5),
    )
    outcome = mechanical_gate(
        candidate_fold_scores=result.per_fold_best_scores,
        baseline_fold_scores=result.baseline_per_fold_scores,
        config=gate_cfg,
    )
    if outcome.accept:
        return {"gate_outcome": outcome}
    return {
        "gate_outcome": outcome,
        "candidate_reject_kind": outcome.reject_kind,
        "candidate_reject_rationale": outcome.rationale,
    }


def verdict_critique_step(state: HypothesizeState, clients: NodeClients) -> HypothesizeState:
    if state.get("candidate_reject_kind") is not None:
        return {}
    gate = state["gate_outcome"]
    if gate is None or not gate.accept:
        return {}
    result = state["candidate_attempt_result"]
    if result is None:
        return {}
    cfg: HypothesisLoopConfig = state["config"]
    payload = VerdictCritiqueInput(
        candidate_name=state["stage1_idea"].candidate_name,
        stage1_idea=state["stage1_idea"],
        aggregate_score=result.aggregate_score,
        baseline_aggregate_score=result.baseline_aggregate_score,
        per_fold_scores=result.per_fold_best_scores,
        baseline_per_fold_scores=result.baseline_per_fold_scores,
        side_effect_flags=list(result.side_effect_flags),
        mechanical_gate=gate,
        delta_params=len(_build_param_intent(state["stage2_parsed"]).added)
        - len(_build_param_intent(state["stage2_parsed"]).removed),
        delta_components=0,
    )
    decision, kind = verdict_critique_node(
        payload=payload,
        client=clients.verdict_critic,
        model=cfg.reasoning_model,
        mechanical_gate_outcome=gate,
    )
    if kind is not None:
        return {
            "verdict_decision": decision,
            "candidate_reject_kind": kind,
            "candidate_reject_rationale": decision.rationale,
        }
    return {"verdict_decision": decision}


def rank_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    """Commit the in-flight candidate to ``accepted`` or ``rejected`` and
    re-sort the accepted set by :func:`rank_score`."""
    now = datetime.now(UTC)
    stage1 = state.get("stage1_idea")
    candidate: HypothesisCandidate | None = None
    if stage1 is not None:
        stage3 = state.get("stage3_parsed")
        files_manifest: dict[str, str] = stage3.files if isinstance(stage3, Stage3Files) else {}
        pi = (
            _build_param_intent(state["stage2_parsed"]).model_dump()
            if state.get("stage2_parsed") is not None
            else {}
        )
        proposed = {
            "param_intent": pi,
            "files_manifest": files_manifest,
        }
        candidate = HypothesisCandidate(
            name=stage1.candidate_name,
            target_metric=(
                state["stage2_parsed"].falsification["primary"]["metric"]
                if state.get("stage2_parsed") is not None
                else "sharpe"
            ),
            falsification=(
                state["stage2_parsed"].falsification
                if state.get("stage2_parsed") is not None
                else {}
            ),
            proposed_change=proposed,
            kb_cites=state.get("kb_cites", []),
            estimated_lift_confidence=stage1.expected_lift_confidence,
        )

    accepted: list[AcceptedHypothesis] = list(state.get("accepted", []))
    rejected: list[RejectedHypothesis] = list(state.get("rejected", []))
    intra: list[HypothesisCandidate] = list(state.get("intra_run_history", []))
    if candidate is not None:
        intra.append(candidate)
        if state.get("candidate_reject_kind") in (None, RejectKind.OK):
            verdict = state.get("verdict_decision")
            attempt = state.get("candidate_attempt_result")
            gate = state.get("gate_outcome")
            accepted.append(
                AcceptedHypothesis(
                    candidate=candidate,
                    rationale=verdict.rationale if verdict is not None else "accepted",
                    evidence={
                        "attempt_result": attempt.model_dump() if attempt is not None else None,
                        "gate": dataclasses.asdict(gate) if gate is not None else None,
                    },
                    accepted_at=now,
                )
            )
        else:
            rejected.append(
                RejectedHypothesis(
                    candidate=candidate,
                    reason=state.get("candidate_reject_rationale", "rejected"),
                    rejected_at=now,
                )
            )
    accepted.sort(key=lambda a: rank_score(a.candidate), reverse=True)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "intra_run_history": intra,
        "iteration": state.get("iteration", 0) + 1,
        # clear in-flight slots
        "stage1_idea": None,  # type: ignore[typeddict-item]
        "stage2_parsed": None,  # type: ignore[typeddict-item]
        "stage3_parsed": None,  # type: ignore[typeddict-item]
        "candidate_attempt_result": None,
        "gate_outcome": None,
        "verdict_decision": None,
        "candidate_reject_kind": None,
        "candidate_reject_rationale": "",
    }


def select_step(state: HypothesizeState, _clients: NodeClients) -> HypothesizeState:
    cfg: HypothesisLoopConfig = state["config"]
    accepted = list(state.get("accepted", []))[: cfg.target_candidates]
    reason = state.get("termination_reason", TerminationReason.RUNNING)
    if reason is TerminationReason.RUNNING:
        reason = (
            TerminationReason.SUFFICIENT_CANDIDATES
            if len(accepted) >= cfg.target_candidates
            else TerminationReason.BUDGET_EXHAUSTED
        )
    return {"accepted": accepted, "termination_reason": reason}


def should_continue(state: HypothesizeState) -> str:
    """Conditional edge after ``rank``.

    Returns ``"continue"`` to loop back into ``generate_stage1_idea`` or
    ``"select"`` to exit the inner loop and finalize. Termination
    priority (per spec):

    1. ``sufficient_candidates`` — accepted count >= target.
    2. ``budget_exhausted`` — iteration counter or max-backtests limit.
    3. otherwise continue.
    """
    cfg: HypothesisLoopConfig = state["config"]
    if len(state.get("accepted", [])) >= cfg.target_candidates:
        state["termination_reason"] = TerminationReason.SUFFICIENT_CANDIDATES
        return "select"
    if state.get("iteration", 0) >= cfg.iteration_budget:
        state["termination_reason"] = TerminationReason.BUDGET_EXHAUSTED
        return "select"
    max_backtests = state.get("max_backtests")
    if max_backtests is not None and state.get("backtests_consumed", 0) >= max_backtests:
        state["termination_reason"] = TerminationReason.BUDGET_EXHAUSTED
        return "select"
    return "continue"


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_workflow(clients: NodeClients) -> Any:  # noqa: ANN401 — langgraph compiled object lacks public type
    """Build and compile the LangGraph ``StateGraph``.

    Each node is a closure capturing ``clients`` so node signatures stay
    LangGraph-compatible (single ``state`` argument). The compiled graph
    is suitable for ``.invoke(state)`` from the orchestrator.
    """

    def _bind(
        fn: Callable[[HypothesizeState, NodeClients], HypothesizeState],
    ) -> Any:  # noqa: ANN401 — langgraph add_node expects a private Runnable union
        def wrapper(state: HypothesizeState) -> HypothesizeState:
            return fn(state, clients)

        wrapper.__name__ = fn.__name__
        return wrapper

    g: StateGraph[HypothesizeState, None, Any, Any] = StateGraph(HypothesizeState)
    g.add_node("diagnose", _bind(diagnose_step))
    g.add_node("kb_query", _bind(kb_query_step))
    g.add_node("kb_filter", _bind(kb_filter_step))
    g.add_node("generate_stage1_idea", _bind(generate_stage1_step))
    g.add_node("cheap_critique", _bind(cheap_critique_step))
    g.add_node("generate_stage2_commitments", _bind(generate_stage2_step))
    g.add_node("generate_stage3_files", _bind(generate_stage3_step))
    g.add_node("mini_optimize", _bind(mini_optimize_step))
    g.add_node("mechanical_gate", _bind(mechanical_gate_step))
    g.add_node("verdict_critique", _bind(verdict_critique_step))
    g.add_node("rank", _bind(rank_step))
    g.add_node("select", _bind(select_step))

    g.set_entry_point("diagnose")
    g.add_edge("diagnose", "kb_query")
    g.add_edge("kb_query", "kb_filter")
    g.add_edge("kb_filter", "generate_stage1_idea")
    g.add_edge("generate_stage1_idea", "cheap_critique")
    g.add_edge("cheap_critique", "generate_stage2_commitments")
    g.add_edge("generate_stage2_commitments", "generate_stage3_files")
    g.add_edge("generate_stage3_files", "mini_optimize")
    g.add_edge("mini_optimize", "mechanical_gate")
    g.add_edge("mechanical_gate", "verdict_critique")
    g.add_edge("verdict_critique", "rank")
    g.add_conditional_edges(
        "rank",
        should_continue,
        {"continue": "generate_stage1_idea", "select": "select"},
    )
    g.add_edge("select", END)

    return g.compile()


# Re-export the repairable-kind predicate for orchestrator callers.
__all__ = [
    "HypothesizeState",
    "NodeClients",
    "compile_workflow",
    "is_repairable",
    "should_continue",
]
