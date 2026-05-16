"""Tester — translate hypotheses into engine inputs.

Phase 10 implementation. Task 10.1 covers the parameter-only fast path:
a hypothesis's ``proposed_change`` describes parameter overrides on an
existing strategy artifact. No build, no lint, no recompile — the
artifact reference passes through unchanged and only the
:class:`~engine::spec::RunSpec`'s ``params`` map shifts. Logic-change
translation (10.2) and the full submit-and-evaluate pipeline (10.3 -
10.6) build on top of this surface.

Why split the parser from the merger:

- The LLM-emitted ``proposed_change`` is opaque JSON
  (``HypothesisCandidate.proposed_change: Any``). The Tester is the
  layer that imposes structure, so parsing is its first responsibility.
- Once parsed, applying the diff is a small, deterministic merge over a
  dict — easy to test in isolation and identical between the
  parameter-only path and any future logic-plus-params hybrid.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .build_pipeline import (
    BuildFailure,
    BuildOutcome,
    StrategyManifest,
    _BuildPipelineLike,
)
from .engine import JobStatus
from .hypothesis_loop import (
    HypothesisCandidate,
    _LedgerLike,
    candidate_to_hypothesis_record,
)
from .types import BacktestMetrics, Bar, DecisionKind, DecisionRecord

# Keys in a ``proposed_change`` that mark it as a logic change rather than
# a parameter-only diff. Presence of any of these forces the Tester to route
# through the build pipeline (task 10.2) instead of the fast path.
_LOGIC_CHANGE_KEYS: frozenset[str] = frozenset(
    {"source", "code", "rewrite", "diff", "patch", "new_strategy"}
)


class ParamDiff(BaseModel):
    """One parameter override.

    ``from_value`` is captured for audit/logging — the run is parameterised
    only by ``to_value``, but recording both means the ledger entry shows
    *what changed*, not just *what was set*.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    param: str
    from_value: Any = Field(default=None, alias="from")
    to_value: Any = Field(alias="to")


class ParamOnlyTranslationError(ValueError):
    """Raised when ``proposed_change`` is not a parameter-only diff."""


def parse_param_only_change(proposed_change: object) -> list[ParamDiff]:
    """Parse a hypothesis's ``proposed_change`` into a list of
    :class:`ParamDiff`.

    Accepted shapes (the LLM is prompted to emit one of):

    - single  ``{"param": str, "from": Any, "to": Any}``
    - bulk    ``{"diffs": [{"param": ..., "from": ..., "to": ...}, ...]}``

    Anything else — a string, a list at top level, or a dict carrying
    keys from :data:`_LOGIC_CHANGE_KEYS` (``source`` / ``code`` /
    ``rewrite`` / ``diff`` / ``patch`` / ``new_strategy``) — raises
    :class:`ParamOnlyTranslationError`. The tester upstream uses this
    error to fall back to the logic-change translation path (10.2)
    instead of treating the failure as fatal.
    """
    if not isinstance(proposed_change, Mapping):
        msg = (
            "proposed_change must be a mapping with `param`+`from`+`to` "
            "or a `diffs` array; got "
            f"{type(proposed_change).__name__}"
        )
        raise ParamOnlyTranslationError(msg)

    logic_keys = _LOGIC_CHANGE_KEYS & set(proposed_change)
    if logic_keys:
        msg = (
            "proposed_change carries logic-change keys "
            f"{sorted(logic_keys)}; route via translate_logic_change "
            "(task 10.2)"
        )
        raise ParamOnlyTranslationError(msg)

    if "diffs" in proposed_change:
        raw_diffs = proposed_change["diffs"]
        if not isinstance(raw_diffs, list):
            msg = "`diffs` must be a list"
            raise ParamOnlyTranslationError(msg)
        return [_diff_from_mapping(item) for item in raw_diffs]

    if "param" in proposed_change:
        return [_diff_from_mapping(proposed_change)]

    msg = (
        "proposed_change must contain `param`+`from`+`to` keys or a "
        "`diffs` array; got keys "
        f"{sorted(proposed_change)}"
    )
    raise ParamOnlyTranslationError(msg)


def _diff_from_mapping(item: object) -> ParamDiff:
    if not isinstance(item, Mapping):
        msg = f"each diff must be a mapping; got {type(item).__name__}"
        raise ParamOnlyTranslationError(msg)
    if "param" not in item or "to" not in item:
        msg = f"diff entries must carry `param` and `to`; got keys {sorted(item)}"
        raise ParamOnlyTranslationError(msg)
    try:
        return ParamDiff.model_validate(dict(item))
    except ValueError as exc:
        raise ParamOnlyTranslationError(str(exc)) from exc


def apply_param_diffs(base_params: Mapping[str, Any], diffs: list[ParamDiff]) -> dict[str, Any]:
    """Return a new params dict with every diff applied over ``base_params``.

    Keys not mentioned in ``diffs`` pass through unchanged. The
    function is order-stable in the diff list, so the last diff for a
    given key wins (the LLM is not expected to emit duplicates, but this
    keeps the operation a well-defined merge).
    """
    merged: dict[str, Any] = dict(base_params)
    for diff in diffs:
        merged[diff.param] = diff.to_value
    return merged


class TranslatedRun(BaseModel):
    """A param-only translation result for one hypothesis.

    Carries the merged params and the parsed diffs so callers (the
    ledger writer; the tester verdict emitter) can record both. The
    ``strategy_artifact`` field is the existing artifact reference,
    forwarded verbatim because no recompile is required.
    """

    model_config = ConfigDict(frozen=True)

    strategy_artifact: str
    params: dict[str, Any]
    diffs: list[ParamDiff]


def translate_param_only(
    candidate: HypothesisCandidate,
    *,
    strategy_artifact: str,
    base_params: Mapping[str, Any],
) -> TranslatedRun:
    """Translate a parameter-only hypothesis into the engine's input shape.

    The strategy artifact reference passes through unchanged — the
    parameter-only fast path is the whole point of this surface
    (`hypothesis-loop::hypothesis-output-schema` allows
    ``proposed_change`` to express either a parameter diff or a logic
    change; 10.1 handles the former). Raises
    :class:`ParamOnlyTranslationError` for any non-parameter shape so
    the caller can route through the logic-change path (10.2) or record
    a structured rejection.
    """
    diffs = parse_param_only_change(candidate.proposed_change)
    return TranslatedRun(
        strategy_artifact=strategy_artifact,
        params=apply_param_diffs(base_params, diffs),
        diffs=diffs,
    )


class LogicChangeTranslationError(ValueError):
    """Raised when a ``proposed_change`` cannot be parsed as a
    logic-change payload (LLM-emitted Rust source + manifest)."""


class LogicChangePayload(BaseModel):
    """Parsed logic-change payload from a hypothesis's ``proposed_change``.

    Mirrors what the LLM is prompted to emit for a logic change: full
    strategy source, the Cargo manifest subset, and an optional parameter
    override map that is merged onto ``base_params`` after the build
    completes. The build pipeline is responsible for compiling ``source``;
    the Tester wraps the result in a :class:`TranslatedRun`.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    manifest: StrategyManifest
    params: dict[str, Any] = Field(default_factory=dict)


def parse_logic_change(proposed_change: object) -> LogicChangePayload:
    """Parse a hypothesis's ``proposed_change`` as a logic-change payload.

    Required shape::

        {
          "source":   "<rust strategy source>",
          "manifest": { "name": ..., "version": ..., "dependencies": [...] },
          "params":   { ... }   # optional param overrides applied post-build
        }

    Raises :class:`LogicChangeTranslationError` for any malformed shape.
    Param-only payloads (no ``source``) are rejected so the caller can
    route through :func:`translate_param_only` (task 10.1) instead.
    """
    if not isinstance(proposed_change, Mapping):
        msg = f"proposed_change must be a mapping; got {type(proposed_change).__name__}"
        raise LogicChangeTranslationError(msg)
    if "source" not in proposed_change:
        msg = (
            "proposed_change is missing `source`; for parameter-only diffs "
            "route through translate_param_only (task 10.1)"
        )
        raise LogicChangeTranslationError(msg)
    if "manifest" not in proposed_change:
        msg = "logic-change proposed_change must carry a `manifest` block"
        raise LogicChangeTranslationError(msg)
    try:
        return LogicChangePayload.model_validate(dict(proposed_change))
    except ValueError as exc:
        raise LogicChangeTranslationError(str(exc)) from exc


class LogicChangeTranslatedRun(BaseModel):
    """A logic-change translation result.

    Carries the compiled artifact reference, merged params, and the build
    pipeline outcome (cache hit vs compiled, plus artifact metadata) so
    the Tester can record both the artifact and how it was obtained.
    """

    model_config = ConfigDict(frozen=True)

    strategy_artifact: str
    params: dict[str, Any]
    payload: LogicChangePayload
    build_outcome: BuildOutcome


def translate_logic_change(
    candidate: HypothesisCandidate,
    *,
    base_params: Mapping[str, Any],
    build_pipeline: _BuildPipelineLike,
) -> LogicChangeTranslatedRun:
    """Translate a logic-change hypothesis into the engine's input shape.

    The LLM-emitted source is pushed through the supplied build pipeline
    (`tester::hypothesis-to-artifact-translation` logic-change scenario).
    The resulting library path becomes the strategy artifact; any
    ``params`` in the payload merge onto ``base_params``. The caller
    catches :class:`~strategy_gpt.build_pipeline.BuildFailure` to record
    a ``rejected: build_failed`` decision (task 10.3).
    """
    payload = parse_logic_change(candidate.proposed_change)
    outcome = build_pipeline.build(payload.source, payload.manifest)
    merged: dict[str, Any] = dict(base_params)
    merged.update(payload.params)
    return LogicChangeTranslatedRun(
        strategy_artifact=outcome.artifact.library_path,
        params=merged,
        payload=payload,
        build_outcome=outcome,
    )


class RejectionReason(StrEnum):
    """Why the Tester rejected a candidate before reporting a verdict.

    Mirrors the two pre-engine failure modes called out in
    `tester::compile-and-lint-validation` and
    `tester::smoke-test-on-a-small-slice`.
    """

    BUILD_FAILED = "build_failed"
    SMOKE_FAILED = "smoke_failed"


class TesterRejection(BaseModel):
    """Result of a pre-engine rejection, persisted to the ledger.

    ``diagnostics`` carries the structured failure detail (build error
    kind + compiler output, smoke panic message + reason, etc.) so a
    future replay can reason about *why* the hypothesis was rejected.
    """

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    decision_id: str
    reason: RejectionReason
    rationale: str
    diagnostics: dict[str, Any]


def _new_id() -> str:
    return uuid.uuid4().hex


def record_tester_rejection(  # noqa: PLR0913 — single orchestration point; mutually relevant args
    ledger: _LedgerLike,
    candidate: HypothesisCandidate,
    *,
    reason: RejectionReason,
    rationale: str,
    diagnostics: Mapping[str, Any],
    now: datetime | None = None,
) -> TesterRejection:
    """Append the hypothesis + a rejected DecisionRecord carrying diagnostics.

    Writes the canonical pair the ledger uses to record any decision: one
    :class:`~strategy_gpt.types.HypothesisRecord` for the candidate and one
    :class:`~strategy_gpt.types.DecisionRecord` with ``kind="rejected"``,
    the rationale, and the diagnostics in ``evidence``. Returns the IDs
    plus the same diagnostics so the caller can hand them back to the
    Hypothesis Loop or surface them in a verdict.
    """
    stamp = now if now is not None else datetime.now(UTC)
    hid = _new_id()
    did = _new_id()
    ledger.record_hypothesis(
        candidate_to_hypothesis_record(candidate, hypothesis_id=hid, created_at=stamp)
    )
    ledger.record_decision(
        DecisionRecord(
            id=did,
            hypothesis_id=hid,
            kind=DecisionKind.REJECTED,
            rationale=rationale,
            evidence=dict(diagnostics),
            decided_at=stamp,
        )
    )
    return TesterRejection(
        hypothesis_id=hid,
        decision_id=did,
        reason=reason,
        rationale=rationale,
        diagnostics=dict(diagnostics),
    )


def reject_build_failure(
    ledger: _LedgerLike,
    candidate: HypothesisCandidate,
    failure: BuildFailure | LogicChangeTranslationError,
    *,
    now: datetime | None = None,
) -> TesterRejection:
    """Record ``rejected: build_failed`` for a candidate whose build or
    payload-parse step failed.

    Logic-change parse failures (the LLM emitted a malformed payload) are
    grouped under the same rejection bucket because the candidate never
    reached the engine — same as `tester::compile-and-lint-validation`.
    """
    if isinstance(failure, BuildFailure):
        diagnostics: dict[str, Any] = {
            "build_error_kind": failure.kind.value,
            "message": failure.message,
        }
        rationale = f"build_failed: {failure.kind.value}"
    else:
        diagnostics = {
            "build_error_kind": "logic_change_parse",
            "message": str(failure),
        }
        rationale = "build_failed: logic_change_parse"
    return record_tester_rejection(
        ledger,
        candidate,
        reason=RejectionReason.BUILD_FAILED,
        rationale=rationale,
        diagnostics=diagnostics,
        now=now,
    )


class _EngineLike(Protocol):
    """Subset of :class:`strategy_gpt.engine.Engine` used by the Tester.

    Declared structurally so unit tests can stub the engine without a
    compiled native extension.
    """

    def submit_batch(
        self,
        artifact_path: str,
        bars: list[Bar],
        spec: dict[str, Any],
        dataset_manifest: str,
        *,
        run_id: str | None = None,
    ) -> str: ...

    def poll(self, handle: str) -> JobStatus: ...

    def drop_handle(self, handle: str) -> bool: ...


class SmokePolicy(BaseModel):
    """Knobs for :func:`run_smoke`.

    Defaults reflect ``tester::smoke-test-on-a-small-slice``: a few weeks
    of bars, at least one simulated trade, and a small timeout. The
    sanity-trip cap is informational — current engine semantics treat
    any sanity-bound trip as a fatal error, so ``max_sanity_trips`` is
    effectively zero. The knob exists so a future relaxation (treat the
    first trip as a warning) plugs in without API churn.
    """

    model_config = ConfigDict(frozen=True)

    min_trades: int = 1
    max_sanity_trips: int = 0
    poll_interval_secs: float = 0.05
    timeout_secs: float = 60.0


class SmokeOutcome(BaseModel):
    """Result of a smoke backtest."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    rationale: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    metrics: BacktestMetrics | None = None


def _build_smoke_spec(  # noqa: PLR0913 — all kwargs are part of the BatchSpec wire shape
    *,
    strategy_artifact: str,
    dataset_ref: str,
    params: Mapping[str, Any],
    slice_start: datetime,
    slice_end: datetime,
    seed: int,
    engine_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "strategy": strategy_artifact,
        "dataset": dataset_ref,
        "runs": [
            {
                "params": dict(params),
                "modes": [{"kind": "plain"}],
                "seed": seed,
                "slice": {
                    "start": slice_start.isoformat(),
                    "end": slice_end.isoformat(),
                },
            }
        ],
        "engine": dict(engine_config) if engine_config is not None else _default_engine_config(),
        "parallelism": 1,
    }


def _default_engine_config() -> dict[str, Any]:
    """Mirrors `engine::spec::EngineConfig::default`."""
    return {
        "fill_model": "NextBarOpen",
        "initial_capital": 100_000.0,
        "commission_per_fill": 0.0,
        "slippage_bps": 0.0,
        "sanity": {"max_intent_size": 1.0e9, "max_position_size": 1.0e9},
    }


def _sanity_violation(message: str) -> bool:
    lower = message.lower()
    return "riskcap" in lower or "sanity bound" in lower or "intent size" in lower


def run_smoke(  # noqa: PLR0913 — all kwargs are part of the BatchSpec wire shape
    engine: _EngineLike,
    *,
    strategy_artifact: str,
    dataset_ref: str,
    bars: list[Bar],
    params: Mapping[str, Any],
    slice_start: datetime,
    slice_end: datetime,
    dataset_manifest: str,
    seed: int = 0,
    engine_config: Mapping[str, Any] | None = None,
    policy: SmokePolicy | None = None,
) -> SmokeOutcome:
    """Submit a single-run batch over a small bar slice and classify the result.

    Failure modes (`tester::smoke-test-on-a-small-slice`):

    - the engine job ``failed`` (panic, OOM, sanity violation) →
      ``smoke_failed`` with the surfaced error message,
    - completed run produced fewer than ``policy.min_trades`` simulated
      trades → ``smoke_failed: no_trades``,
    - cancellation or timeout while polling → ``smoke_failed: timeout``.

    On success the metrics dict is returned so the Tester can short-circuit
    a no-op full batch if the smoke run already covers the slice the
    Hypothesis Loop cares about.
    """
    pol = policy if policy is not None else SmokePolicy()
    spec = _build_smoke_spec(
        strategy_artifact=strategy_artifact,
        dataset_ref=dataset_ref,
        params=params,
        slice_start=slice_start,
        slice_end=slice_end,
        seed=seed,
        engine_config=engine_config,
    )
    handle = engine.submit_batch(strategy_artifact, bars, spec, dataset_manifest)
    deadline = time.monotonic() + pol.timeout_secs
    try:
        while True:
            status = engine.poll(handle)
            if status.status == "completed":
                return _classify_smoke_completed(status, pol)
            if status.status == "failed":
                error = status.error or "unknown engine failure"
                kind = "sanity" if _sanity_violation(error) else "engine_failed"
                return SmokeOutcome(
                    ok=False,
                    rationale=f"smoke_failed: {kind}",
                    diagnostics={"kind": kind, "message": error},
                )
            if status.status == "cancelled":
                return SmokeOutcome(
                    ok=False,
                    rationale="smoke_failed: cancelled",
                    diagnostics={"kind": "cancelled"},
                )
            if time.monotonic() >= deadline:
                return SmokeOutcome(
                    ok=False,
                    rationale="smoke_failed: timeout",
                    diagnostics={"kind": "timeout", "timeout_secs": pol.timeout_secs},
                )
            time.sleep(pol.poll_interval_secs)
    finally:
        engine.drop_handle(handle)


def _classify_smoke_completed(status: JobStatus, policy: SmokePolicy) -> SmokeOutcome:
    results = status.results or []
    if not results:
        return SmokeOutcome(
            ok=False,
            rationale="smoke_failed: empty_results",
            diagnostics={"kind": "empty_results"},
        )
    entry = results[0]
    if entry.get("status") == "failed":
        return SmokeOutcome(
            ok=False,
            rationale="smoke_failed: run_failed",
            diagnostics={
                "kind": "run_failed",
                "error_kind": entry.get("error_kind", "unknown"),
                "message": entry.get("message", ""),
            },
        )
    result = entry.get("result", entry)
    trades = result.get("trades", [])
    metrics_raw = result.get("metrics")
    exec_log = result.get("exec_log", [])
    sanity_trips = sum(
        1
        for ev in exec_log
        if isinstance(ev, Mapping)
        and isinstance(ev.get("event"), str)
        and "sanity" in ev["event"].lower()
    )
    if sanity_trips > policy.max_sanity_trips:
        return SmokeOutcome(
            ok=False,
            rationale="smoke_failed: sanity_trips",
            diagnostics={"kind": "sanity_trips", "count": sanity_trips},
        )
    if len(trades) < policy.min_trades:
        return SmokeOutcome(
            ok=False,
            rationale="smoke_failed: no_trades",
            diagnostics={
                "kind": "no_trades",
                "trades_observed": len(trades),
                "min_trades": policy.min_trades,
            },
        )
    metrics = BacktestMetrics.model_validate(metrics_raw) if metrics_raw is not None else None
    return SmokeOutcome(
        ok=True,
        rationale="smoke_passed",
        diagnostics={"trades": len(trades)},
        metrics=metrics,
    )


def walk_forward_slices(
    start: datetime, end: datetime, folds: int
) -> list[tuple[datetime, datetime]]:
    """Split ``[start, end)`` into ``folds`` equal half-open slices.

    Used by :func:`build_full_batch_spec` to populate one
    :class:`RunSpec` per fold. The last fold absorbs any rounding
    remainder so the union of slices is always exactly ``[start, end)``.
    """
    if folds < 1:
        msg = f"folds must be >= 1; got {folds}"
        raise ValueError(msg)
    if end <= start:
        msg = f"slice must be a forward-going range; got start={start}, end={end}"
        raise ValueError(msg)
    total = (end - start).total_seconds()
    step = total / folds
    out: list[tuple[datetime, datetime]] = []
    for i in range(folds):
        s = start + timedelta(seconds=step * i)
        e = end if i == folds - 1 else start + timedelta(seconds=step * (i + 1))
        out.append((s, e))
    return out


def build_full_batch_spec(  # noqa: PLR0913 — all kwargs are part of the BatchSpec wire shape
    *,
    strategy_artifact: str,
    dataset_ref: str,
    params: Mapping[str, Any],
    slice_start: datetime,
    slice_end: datetime,
    folds: int = 1,
    stress_modes: list[Mapping[str, Any]] | None = None,
    sensitivity_modes: list[Mapping[str, Any]] | None = None,
    engine_config: Mapping[str, Any] | None = None,
    seed: int = 0,
    parallelism: int = 1,
) -> dict[str, Any]:
    """Construct a `BatchSpec` JSON dict for the full test run.

    Mirrors `tester::batch-delegation-to-the-engine`: fold slices
    plus the configured stress and sensitivity modes. ``stress_modes`` is
    a list of raw mode dicts (``{"kind": "monte_carlo", "n": 200,
    "block_size": 5}`` / ``{"kind": "slippage", "bps_grid": [...]}`` /
    ``{"kind": "regime_filter", "ranges": [...]}``); ``sensitivity_modes``
    is the same shape with ``{"kind": "sensitivity", "param": ...,
    "values": [...]}`` entries. The tester forwards whatever the LLM
    objective spec asked for; the engine deserialises into
    `engine::spec::Mode`.
    """
    slices = walk_forward_slices(slice_start, slice_end, folds)
    modes: list[Mapping[str, Any]] = [{"kind": "plain"}]
    modes.extend(stress_modes or [])
    modes.extend(sensitivity_modes or [])
    runs: list[dict[str, Any]] = [
        {
            "params": dict(params),
            "modes": [dict(m) for m in modes],
            "seed": seed + i,
            "slice": {"start": s.isoformat(), "end": e.isoformat()},
        }
        for i, (s, e) in enumerate(slices)
    ]
    return {
        "strategy": strategy_artifact,
        "dataset": dataset_ref,
        "runs": runs,
        "engine": dict(engine_config) if engine_config is not None else _default_engine_config(),
        "parallelism": parallelism,
    }


# ---------------------------------------------------------------------------
# Verdict evaluation (task 10.6)
# ---------------------------------------------------------------------------


_FALSIFICATION_OPS: frozenset[str] = frozenset({">=", ">", "<=", "<", "==", "!="})

ComparisonOp = Literal[">=", ">", "<=", "<", "==", "!="]


class FalsificationCriterion(BaseModel):
    """Structured form of a hypothesis's falsification criterion.

    The LLM emits one of two shapes:

    - ``{"metric": "sharpe", "op": ">=", "threshold": 1.5}``
    - ``{"op": ">=", "threshold": 1.5}`` (metric inferred from
      ``candidate.target_metric``)

    `tester::verdict-reporting` requires the verdict to name the metric,
    the comparison, and the observed value, so the parsed form keeps all
    three explicit.
    """

    model_config = ConfigDict(frozen=True)

    metric: str
    op: ComparisonOp
    threshold: float


class FalsificationParseError(ValueError):
    """Raised when ``candidate.falsification`` cannot be parsed."""


def parse_falsification(raw: object, *, default_metric: str) -> FalsificationCriterion:
    if not isinstance(raw, Mapping):
        msg = (
            "falsification must be a mapping with `op`+`threshold` "
            f"(metric optional); got {type(raw).__name__}"
        )
        raise FalsificationParseError(msg)
    op = raw.get("op")
    if op not in _FALSIFICATION_OPS:
        msg = f"falsification `op` must be one of {sorted(_FALSIFICATION_OPS)}; got {op!r}"
        raise FalsificationParseError(msg)
    threshold = raw.get("threshold")
    if not isinstance(threshold, int | float):
        msg = f"falsification `threshold` must be numeric; got {type(threshold).__name__}"
        raise FalsificationParseError(msg)
    metric = raw.get("metric", default_metric)
    if not isinstance(metric, str) or not metric:
        msg = "falsification `metric` must be a non-empty string"
        raise FalsificationParseError(msg)
    return FalsificationCriterion(metric=metric, op=op, threshold=float(threshold))


class VerdictKind(StrEnum):
    """Pass/fail outcome of a hypothesis test."""

    PASSED = "passed"
    FAILED = "failed"


class Verdict(BaseModel):
    """Final hypothesis verdict (`tester::verdict-reporting`).

    Carries the parsed criterion, the observed metric value, and a
    human-readable rationale so the Hypothesis Loop can persist it to the
    ledger and surface it in the next iteration's context.
    """

    model_config = ConfigDict(frozen=True)

    kind: VerdictKind
    criterion: FalsificationCriterion
    observed: float
    rationale: str


def _compare(op: str, observed: float, threshold: float) -> bool:
    if op == ">=":
        return observed >= threshold
    if op == ">":
        return observed > threshold
    if op == "<=":
        return observed <= threshold
    if op == "<":
        return observed < threshold
    if op == "==":
        return observed == threshold
    if op == "!=":
        return observed != threshold
    msg = f"unknown comparison operator `{op}`"  # pragma: no cover
    raise ValueError(msg)


def evaluate_verdict(
    candidate: HypothesisCandidate,
    metrics: BacktestMetrics | Mapping[str, Any],
) -> Verdict:
    """Score ``metrics`` against ``candidate.falsification`` and return a verdict.

    The criterion is parsed against the candidate's ``target_metric`` as
    the default; the LLM may override by including a ``metric`` key in
    the falsification payload (useful when the hypothesis targets one
    metric but is falsified against another, e.g. "improves Sharpe iff
    max_drawdown stays below 0.20"). A missing metric on the engine
    output raises :class:`FalsificationParseError` so the caller can
    record a structured rejection rather than a silent default.
    """
    criterion = parse_falsification(candidate.falsification, default_metric=candidate.target_metric)
    metrics_map: Mapping[str, Any] = (
        metrics.model_dump() if isinstance(metrics, BacktestMetrics) else metrics
    )
    if criterion.metric not in metrics_map:
        msg = f"engine metrics do not carry `{criterion.metric}`; available: {sorted(metrics_map)}"
        raise FalsificationParseError(msg)
    observed = float(metrics_map[criterion.metric])
    passed = _compare(criterion.op, observed, criterion.threshold)
    kind = VerdictKind.PASSED if passed else VerdictKind.FAILED
    rationale = (
        f"{criterion.metric}={observed:g} {'satisfies' if passed else 'fails'} "
        f"{criterion.op} {criterion.threshold:g}"
    )
    return Verdict(kind=kind, criterion=criterion, observed=observed, rationale=rationale)


def attempt_logic_change(
    ledger: _LedgerLike,
    build_pipeline: _BuildPipelineLike,
    candidate: HypothesisCandidate,
    *,
    base_params: Mapping[str, Any],
    now: datetime | None = None,
) -> LogicChangeTranslatedRun | TesterRejection:
    """Translate a logic-change candidate or record a structured rejection.

    Wraps :func:`translate_logic_change` so build failures and malformed
    payloads land in the ledger as ``rejected: build_failed`` decisions
    before the engine is invoked (`tester::compile-and-lint-validation`).
    """
    try:
        return translate_logic_change(
            candidate, base_params=base_params, build_pipeline=build_pipeline
        )
    except (BuildFailure, LogicChangeTranslationError) as exc:
        return reject_build_failure(ledger, candidate, exc, now=now)


__all__ = [
    "FalsificationCriterion",
    "FalsificationParseError",
    "LogicChangePayload",
    "LogicChangeTranslatedRun",
    "LogicChangeTranslationError",
    "ParamDiff",
    "ParamOnlyTranslationError",
    "RejectionReason",
    "SmokeOutcome",
    "SmokePolicy",
    "TesterRejection",
    "TranslatedRun",
    "Verdict",
    "VerdictKind",
    "apply_param_diffs",
    "attempt_logic_change",
    "build_full_batch_spec",
    "evaluate_verdict",
    "parse_falsification",
    "parse_logic_change",
    "parse_param_only_change",
    "record_tester_rejection",
    "reject_build_failure",
    "run_smoke",
    "translate_logic_change",
    "translate_param_only",
    "walk_forward_slices",
]
