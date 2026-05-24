"""Hypothesize orchestrator entry.

``hypothesize(strategy, *, ledger, kb, config, persist=True)`` is the
top-level entry the CLI subcommand and Python callers use to drive the
hypothesis loop end-to-end:

1. Bootstrap state from ``ledger.recent_decisions(strategy=...)``.
2. Load (or compute) the per-strategy baseline-best result.
3. Compile the LangGraph :class:`StateGraph` from
   :mod:`strategy_gpt.workflow` and invoke it.
4. Persist accepted/rejected decisions, source blobs, and response
   blobs under the per-strategy ledger layout
   (`experiment-ledger::per-strategy-storage-layout`).

This module is dependency-injection heavy: the orchestrator does not
know how to fetch bars, build artifacts, or run engine jobs. Callers
hand it a :class:`HypothesizeDeps` bag carrying every collaborator;
unit tests construct the bag with stubs.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .build_pipeline import _BuildPipelineLike
from .hypothesis_loop import (
    AcceptedHypothesis,
    PriorDecision,
    RejectedHypothesis,
    TerminationReason,
)
from .kb_query import KbClient
from .per_strategy_ledger import (
    STAGE1,
    STAGE2,
    STAGE3,
    DecisionRecordV2,
    DecisionStage,
    HypothesisRecordV2,
    PerStrategyLedger,
    StageResponses,
    canonical_files_set_hash,
)
from .reasoning import HypothesisLoopConfig
from .reasoning_clients import StageReasoningClient
from .tester import EvaluateFoldFn
from .types import BacktestResult, DecisionKind
from .verdict_critique import (
    DeterministicVerdictCritic,
    VerdictCritiqueClient,
)
from .workflow import HypothesizeState, NodeClients, compile_workflow

# ---------------------------------------------------------------------------
# Dependency bag for the orchestrator
# ---------------------------------------------------------------------------


@dataclass
class HypothesizeDeps:
    """Collaborators the orchestrator threads into the workflow.

    Mirrors :class:`workflow.NodeClients` plus the few extras the
    orchestrator itself needs (baseline computation, ledger root). The
    duplication keeps the workflow module ignorant of file paths and
    baseline-computation policy.
    """

    kb: KbClient
    stage_client: StageReasoningClient
    build_pipeline: _BuildPipelineLike
    evaluate_fold: EvaluateFoldFn
    prompt_api: str
    allowed_metrics: list[str]
    baseline_result: BacktestResult
    baseline_files: Mapping[str, str]
    baseline_params_schema: dict[str, Any] | None
    baseline_per_fold_scores: Sequence[float]
    baseline_metrics: Mapping[str, float]
    baseline_aggregate_score: float
    objective_metric: str
    dataset_manifest_hash: str
    kept_bounds: Mapping[str, Any] = field(default_factory=dict)
    verdict_critic: VerdictCritiqueClient | None = None
    engine_rt_src_dir: Path | None = None
    """Path to ``crates/engine-rt/src/``.

    Forwarded to :class:`workflow.NodeClients` so the stage-3 prompt
    can embed the authoritative trait surface. Optional; when
    ``None``, the stage-3 prompt falls back to PROMPT_API alone."""


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypothesizeResult:
    """Summary returned by :func:`hypothesize`.

    Mirrors what the CLI prints to stdout. The full state is available
    via :attr:`state` for callers that want to inspect intermediate
    fields without re-running the graph.
    """

    strategy: str
    accepted: list[AcceptedHypothesis]
    rejected: list[RejectedHypothesis]
    termination_reason: TerminationReason
    iterations: int
    backtests_consumed: int
    persisted_decision_ids: list[str]
    state: HypothesizeState


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _project_prior_decisions(ledger: PerStrategyLedger, limit: int) -> list[PriorDecision]:
    """Read the per-strategy decision log and project to
    :class:`PriorDecision` records.

    The per-strategy ledger stores richer records than the legacy
    `PriorDecision` type carries; we project the subset the workflow
    consumes (KB cites, kind, rationale, decided_at). When the
    per-strategy decision file does not exist yet (first run for the
    strategy), returns an empty list.
    """
    out: list[PriorDecision] = []
    for decision in ledger.recent_decisions(limit=limit):
        # Deferred candidates (mechanical code-emission failures)
        # carry no signal about the idea's quality and MUST NOT bias
        # future ideation. Spec: hypothesis-loop::mechanical-failures-
        # are-deferred-not-rejected.
        if decision.outcome.kind == DecisionKind.DEFERRED.value:
            continue
        # Pull the matching hypothesis (record_json is full V2 shape).
        # ``hypothesis_id`` may not have a corresponding hypothesis row
        # if the parquet file got truncated; tolerate.
        match = next(
            (h for h in ledger.hypotheses_iter() if h.id == decision.hypothesis_id),
            None,
        )
        if match is None:
            continue
        from .types import HypothesisRecord  # noqa: PLC0415 — avoid cycle

        legacy_hyp = HypothesisRecord(
            id=match.id,
            name=match.candidate_name,
            target_metric=match.falsification.primary.metric,
            falsification=match.falsification.model_dump(),
            proposed_change={
                "files_manifest": match.files_manifest,
                "deleted_files": list(match.deleted_files),
                "param_intent": match.param_intent.model_dump(),
                "baseline_files_hash": match.baseline_files_hash,
            },
            kb_cites=list(match.kb_cites),
            created_at=match.created_at,
        )
        out.append(
            PriorDecision(
                decision_id=decision.id,
                kind=DecisionKind.ACCEPTED
                if decision.outcome.kind == "accepted"
                else DecisionKind.REJECTED,
                rationale=decision.rationale,
                evidence=decision.evidence,
                decided_at=decision.decided_at,
                hypothesis=legacy_hyp,
            )
        )
    return out


def _load_or_compute_baseline(
    ledger: PerStrategyLedger,
    *,
    dataset_manifest_hash: str,
    deps: HypothesizeDeps,
) -> dict[str, Any]:
    """Load baseline-best from the per-strategy cache or persist a fresh
    one from ``deps`` and return it.

    The cache key is ``dataset_manifest_hash``; a cached entry under a
    different manifest is treated as a miss (see PerStrategyLedger
    docstring) so the comparison stays apples-to-apples.
    """

    def compute() -> dict[str, Any]:
        return {
            "objective_metric": deps.objective_metric,
            "aggregate_score": deps.baseline_aggregate_score,
            "per_fold_scores": list(deps.baseline_per_fold_scores),
            "metrics": dict(deps.baseline_metrics),
            "files_set_hash": canonical_files_set_hash(deps.baseline_files),
            "computed_at": datetime.now(UTC).isoformat(),
        }

    return ledger.baseline_best(
        dataset_manifest_hash=dataset_manifest_hash,
        compute_on_miss=compute,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_baseline_files(ledger: PerStrategyLedger, files: Mapping[str, str]) -> str:
    return ledger.write_source_set(files)


def _persist_candidate(  # noqa: PLR0913 — orchestration seam, mutually relevant kwargs
    ledger: PerStrategyLedger,
    *,
    strategy: str,
    state: HypothesizeState,
    decision_kind: DecisionKind,
    rationale: str,
    evidence: dict[str, Any],
    baseline_files_hash: str,
) -> str:
    """Write hypothesis row + decision row + source blobs + response blobs
    for one accepted/rejected candidate. Returns the decision id."""
    decision_id = uuid.uuid4().hex
    hypothesis_id = uuid.uuid4().hex
    now = datetime.now(UTC)

    files = state["stage3_parsed"].files if state.get("stage3_parsed") is not None else {}
    files_set_hash = ledger.write_source_set(files) if files else baseline_files_hash
    stage1_hash = (
        ledger.write_response_blob(decision_id, STAGE1, state.get("stage1_response", ""))
        if state.get("stage1_response")
        else ""
    )
    stage2_hash = (
        ledger.write_response_blob(decision_id, STAGE2, state.get("stage2_response", ""))
        if state.get("stage2_response")
        else ""
    )
    stage3_hash = (
        ledger.write_response_blob(decision_id, STAGE3, state.get("stage3_response", ""))
        if state.get("stage3_response")
        else ""
    )

    stage2 = state.get("stage2_parsed")
    from .per_strategy_ledger import (  # noqa: PLC0415 — avoid cycle on small types
        AddedParam,
        Falsification,
        FalsificationPrimary,
        GuardConstraint,
        ParamIntent,
    )

    if stage2 is not None:
        primary_raw = stage2.falsification["primary"]
        falsification = Falsification(
            primary=FalsificationPrimary(
                metric=primary_raw["metric"],
                direction=primary_raw["direction"],
                delta_vs_baseline=float(primary_raw["delta_vs_baseline"]),
            ),
            guard_constraints=[
                GuardConstraint(**g) for g in stage2.falsification.get("guard_constraints", [])
            ],
        )
        param_intent = ParamIntent(
            added=[AddedParam(**a) for a in stage2.param_intent.get("added", [])],
            kept=list(stage2.param_intent.get("kept", [])),
            removed=list(stage2.param_intent.get("removed", [])),
        )
    else:
        falsification = Falsification(
            primary=FalsificationPrimary(metric="sharpe", direction="gt", delta_vs_baseline=0.0),
        )
        param_intent = ParamIntent()

    stage1 = state.get("stage1_idea")
    candidate_name = stage1.candidate_name if stage1 is not None else "unknown"
    rationale_text = (stage1.rationale if stage1 is not None else rationale)[:500]
    confidence = stage1.expected_lift_confidence if stage1 is not None else 0.0
    side_effects = list(stage1.expected_side_effects) if stage1 is not None else []

    files_manifest = {
        path: canonical_files_set_hash({path: content}) for path, content in files.items()
    }
    deleted_files = list(state["stage3_parsed"].deleted) if state.get("stage3_parsed") else []

    hyp = HypothesisRecordV2(
        id=hypothesis_id,
        strategy=strategy,
        candidate_name=candidate_name,
        files_manifest=files_manifest,
        deleted_files=deleted_files,
        baseline_files_hash=baseline_files_hash,
        param_intent=param_intent,
        falsification=falsification,
        expected_lift_confidence=confidence,
        expected_side_effects=side_effects,
        rationale=rationale_text,
        stage_responses=StageResponses(
            stage1_hash=stage1_hash,
            stage2_hash=stage2_hash,
            stage3_hash=stage3_hash,
        ),
        kb_cites=[c.model_dump() for c in state.get("kb_cites", [])],
        created_at=now,
    )
    ledger.record_hypothesis(hyp)

    decision = DecisionRecordV2(
        id=decision_id,
        hypothesis_id=hypothesis_id,
        strategy=strategy,
        outcome=DecisionStage(
            kind=decision_kind.value,
            stage=(
                evidence.get("reject_kind")
                if decision_kind in (DecisionKind.REJECTED, DecisionKind.DEFERRED)
                else None
            ),
        ),
        rationale=rationale,
        evidence={**evidence, "files_set_hash": files_set_hash},
        decided_at=now,
    )
    ledger.record_decision(decision)
    return decision_id


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


ProgressCallback = Callable[[str, Mapping[str, Any], Mapping[str, Any]], None]
"""Per-node progress hook.

Signature: ``(node_name, delta, state) -> None``. ``delta`` is the
node's state contribution; ``state`` is the cumulative state after the
node ran. Callers use this to render human-readable progress without
parsing the workflow internals.
"""


def hypothesize(  # noqa: PLR0913 — top-level orchestration entry, mutually relevant args
    strategy: str,
    *,
    ledger: PerStrategyLedger,
    deps: HypothesizeDeps,
    config: HypothesisLoopConfig,
    persist: bool = True,
    max_backtests: int | None = None,
    prior_decision_limit: int = 50,
    progress: ProgressCallback | None = None,
    attempt_sink: Callable[[str], None] | None = None,
) -> HypothesizeResult:
    """Drive the hypothesis loop end-to-end.

    Bootstrap state from the per-strategy ledger, load (or compute) the
    baseline-best, compile and invoke the LangGraph workflow, then
    optionally persist accepted/rejected decisions to the per-strategy
    layout.

    ``max_backtests`` enforces the iteration-time ceiling
    (`hypothesis-loop::iteration-budget-honored`). The orchestrator
    checks the budget at iteration start; the workflow's mini-optimize
    node also short-circuits when the next iteration would exceed it.
    """
    prior_decisions = _project_prior_decisions(ledger, limit=prior_decision_limit)
    baseline = _load_or_compute_baseline(
        ledger,
        dataset_manifest_hash=deps.dataset_manifest_hash,
        deps=deps,
    )
    baseline_files_hash = baseline.get("files_set_hash", "")
    if persist and not baseline_files_hash:
        baseline_files_hash = _persist_baseline_files(ledger, deps.baseline_files)

    clients = NodeClients(
        kb=deps.kb,
        stage_client=deps.stage_client,
        build_pipeline=deps.build_pipeline,
        evaluate_fold=deps.evaluate_fold,
        verdict_critic=deps.verdict_critic or DeterministicVerdictCritic(),
        prompt_api=deps.prompt_api,
        allowed_metrics=deps.allowed_metrics,
        baseline_files=deps.baseline_files,
        baseline_params_schema=deps.baseline_params_schema,
        baseline_per_fold_scores=deps.baseline_per_fold_scores,
        baseline_metrics=deps.baseline_metrics,
        baseline_aggregate_score=deps.baseline_aggregate_score,
        objective_metric=deps.objective_metric,
        kept_bounds=deps.kept_bounds,
        engine_rt_src_dir=deps.engine_rt_src_dir,
        progress_sink=attempt_sink,
    )

    initial: HypothesizeState = {
        "strategy": strategy,
        "baseline_result": deps.baseline_result,
        "kb_cites": [],
        "prior_decisions": prior_decisions,
        "accepted": [],
        "rejected": [],
        "intra_run_history": [],
        "iteration": 0,
        "backtests_consumed": 0,
        "termination_reason": TerminationReason.RUNNING,
        "config": config,
        "max_backtests": max_backtests,
    }

    graph = compile_workflow(clients)
    # LangGraph default recursion_limit is 25; raise so the inner loop
    # can iterate up to the configured budget without tripping it.
    recursion = max(25, 12 * (config.iteration_budget + 1))
    if progress is None:
        final_state = graph.invoke(initial, {"recursion_limit": recursion})
    else:
        final_state = dict(initial)
        for chunk in graph.stream(
            initial,
            {"recursion_limit": recursion},
            stream_mode=["updates", "values"],
        ):
            mode, payload = chunk
            if mode == "values" and isinstance(payload, dict):
                final_state = payload
            elif mode == "updates" and isinstance(payload, dict):
                for node_name, delta in payload.items():
                    if isinstance(delta, dict):
                        progress(node_name, delta, final_state)

    persisted_ids: list[str] = []
    if persist:
        for accepted in final_state.get("accepted", []):
            decision_id = _persist_candidate(
                ledger,
                strategy=strategy,
                state=_snapshot_for_persist(final_state, accepted=accepted),
                decision_kind=DecisionKind.ACCEPTED,
                rationale=accepted.rationale,
                evidence=accepted.evidence or {},
                baseline_files_hash=baseline_files_hash,
            )
            persisted_ids.append(decision_id)
        for rejected in final_state.get("rejected", []):
            # Mechanical (code-emission) failures persist as ``deferred``
            # so the candidate's idea + commitments are preserved without
            # biasing future ideation. Spec: hypothesis-loop::mechanical-
            # failures-are-deferred-not-rejected.
            from .reject_taxonomy import is_mechanical  # noqa: PLC0415

            decision_kind = (
                DecisionKind.DEFERRED
                if rejected.reject_kind is not None and is_mechanical(rejected.reject_kind)
                else DecisionKind.REJECTED
            )
            decision_id = _persist_candidate(
                ledger,
                strategy=strategy,
                state=_snapshot_for_persist(final_state, rejected=rejected),
                decision_kind=decision_kind,
                rationale=rejected.reason,
                evidence={"reject_kind": rejected.reject_kind} if rejected.reject_kind else {},
                baseline_files_hash=baseline_files_hash,
            )
            persisted_ids.append(decision_id)

    return HypothesizeResult(
        strategy=strategy,
        accepted=list(final_state.get("accepted", [])),
        rejected=list(final_state.get("rejected", [])),
        termination_reason=final_state.get(
            "termination_reason", TerminationReason.BUDGET_EXHAUSTED
        ),
        iterations=final_state.get("iteration", 0),
        backtests_consumed=final_state.get("backtests_consumed", 0),
        persisted_decision_ids=persisted_ids,
        state=final_state,
    )


def _snapshot_for_persist(
    state: HypothesizeState,
    *,
    accepted: AcceptedHypothesis | None = None,
    rejected: RejectedHypothesis | None = None,
) -> HypothesizeState:
    """Build a minimal in-flight snapshot referencing a finalized candidate.

    The workflow clears in-flight slots after each iteration; persist
    references the candidate's stage-1 idea + stage-2 commitments
    rebuilt from the final ``proposed_change`` payload (no LLM data is
    lost — the original stage emissions are not recoverable from the
    state once cleared, so this snapshot persists what the workflow
    captured into the candidate record).
    """
    if accepted is not None:
        entry = accepted.candidate
    elif rejected is not None:
        entry = rejected.candidate
    else:
        entry = None
    snap: HypothesizeState = dict(state)  # type: ignore[assignment]
    if entry is None:
        return snap
    snap["kb_cites"] = list(entry.kb_cites)
    # Minimal stub stage1/2/3 so the persistor can populate the
    # per-strategy record without crashing on missing fields. Response
    # blobs persist as empty strings when the workflow has dropped them.
    from .markdown_io import Stage1Idea, Stage2Commitments, Stage3Files  # noqa: PLC0415

    if accepted is not None:
        rationale_text = accepted.rationale[:500]
    elif rejected is not None:
        rationale_text = rejected.reason[:500]
    else:
        rationale_text = ""
    snap["stage1_idea"] = Stage1Idea(
        candidate_name=entry.name,
        rationale=rationale_text,
        expected_lift_confidence=entry.estimated_lift_confidence,
        expected_side_effects=[],
    )
    pc = entry.proposed_change if isinstance(entry.proposed_change, dict) else {}
    pi = pc.get("param_intent", {}) if isinstance(pc, dict) else {}
    default_primary = {
        "primary": {
            "metric": entry.target_metric,
            "direction": "gt",
            "delta_vs_baseline": 0.0,
        }
    }
    snap["stage2_parsed"] = Stage2Commitments(
        falsification=entry.falsification
        if isinstance(entry.falsification, dict)
        else default_primary,
        param_intent=pi if isinstance(pi, dict) else {},
    )
    files_manifest = pc.get("files_manifest", {}) if isinstance(pc, dict) else {}
    snap["stage3_parsed"] = Stage3Files(
        files=dict.fromkeys(files_manifest, "") if isinstance(files_manifest, dict) else {},
        deleted=pc.get("deleted_files", []) if isinstance(pc, dict) else [],
    )
    snap.setdefault("stage1_response", "")
    snap.setdefault("stage2_response", "")
    snap.setdefault("stage3_response", "")
    return snap


def hypothesize_result_to_json(result: HypothesizeResult) -> str:
    """Render a :class:`HypothesizeResult` as JSON for CLI stdout."""
    payload = {
        "strategy": result.strategy,
        "termination_reason": result.termination_reason.value,
        "iterations": result.iterations,
        "backtests_consumed": result.backtests_consumed,
        "n_accepted": len(result.accepted),
        "n_rejected": len(result.rejected),
        "accepted": [{"name": a.candidate.name, "rationale": a.rationale} for a in result.accepted],
        "rejected": [{"name": r.candidate.name, "reason": r.reason} for r in result.rejected],
        "persisted_decision_ids": list(result.persisted_decision_ids),
    }
    return json.dumps(payload, indent=2, default=str)


__all__ = [
    "HypothesizeDeps",
    "HypothesizeResult",
    "ProgressCallback",
    "hypothesize",
    "hypothesize_result_to_json",
]
