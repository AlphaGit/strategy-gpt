"""Optimizer rationale generator.

Spec `param-optimizer::optimized-output-and-rationale`: an optimization run
returns the best parameter set together with a natural-language rationale
that references both optimizer-observed surface properties and KB
citations.

The rationale generator is decoupled from the LLM:

- :class:`RationaleClient` is a structural protocol — implementations call
  whichever reasoning model the operator has configured. The orchestrator
  wires the same Anthropic/OpenAI surface it uses for the hypothesis loop.
- :func:`build_rationale_inputs` computes the optimizer-observed surface
  features (parameter-vs-score correlations, plateau detection, top-trial
  spread) from the :class:`OptimizerResult`. This is pure Python; tests
  exercise it without an LLM.
- :func:`generate_rationale` ties them together: surface features + KB
  retrieval → LLM. Falls back to a deterministic template when the
  client is :class:`TemplateRationaleClient`, so an offline run still
  produces a rationale string.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Protocol

from .hypothesis_loop import KbCitation
from .kb_query import KbClient, provenance_to_citation
from .optimizer import OptimizerResult, Trial

# Surface-feature thresholds. Kept as constants so the heuristics are
# auditable from the ledger entry that records the rationale.
_MIN_TRIALS_FOR_CORRELATION = 4
_CORRELATION_REPORT_THRESHOLD = 0.2
# Default top-k for the rationale's own KB consultation. Small because the
# rationale prompt has a tight token budget and the citations are summary
# context, not the primary evidence.
_DEFAULT_KB_TOP_K = 4
_PLATEAU_RATIO_THRESHOLD = 0.5
_TIGHT_FEASIBLE_REGION_THRESHOLD = 0.5
_MIN_OBS_FOR_PEARSON = 2


@dataclass(frozen=True)
class SurfaceFeature:
    """One observable property of the optimizer's parameter surface."""

    description: str
    """Short human-readable summary, e.g.
    ``"lookback correlates +0.42 with sharpe across 24 accepted trials"``."""

    weight: float = 1.0
    """Relative importance for the rationale prompt; higher = more salient."""


@dataclass(frozen=True)
class RationaleInputs:
    """Bundle handed to the rationale LLM (or template)."""

    best_params: dict[str, object]
    best_score: float
    surface_features: list[SurfaceFeature]
    citations: list[KbCitation] = field(default_factory=list)
    accepted_trial_count: int = 0
    rejected_trial_count: int = 0


class RationaleClient(Protocol):
    """LLM-backed rationale-generation surface."""

    def write_rationale(self, inputs: RationaleInputs) -> str: ...


def _kb_query_for_best(result: OptimizerResult, *, strategy_name: str | None) -> str:
    """Compose a KB query from the optimizer's chosen region.

    Joins the strategy name (when supplied) with the best-trial parameter
    names. Values are intentionally omitted — the KB indexes concepts, not
    numeric ranges, so ``"vxx vol_lo vol_hi"`` retrieves the volatility-
    regime note while ``"vxx 0.008 0.05"`` would not.
    """
    if result.best is None:
        return strategy_name or ""
    parts: list[str] = []
    if strategy_name:
        parts.append(strategy_name)
    parts.extend(sorted(result.best.params.keys()))
    return " ".join(parts).strip()


def _retrieve_kb_citations(
    result: OptimizerResult,
    *,
    kb_client: KbClient,
    strategy_name: str | None,
    k: int,
) -> list[KbCitation]:
    """Run one retrieval call against the KB and project to citations.

    Empty result (no best trial → empty query) returns ``[]``. Errors are
    not swallowed: a misconfigured KB should surface immediately so the
    operator fixes it before the rationale is recorded into the ledger.
    """
    query = _kb_query_for_best(result, strategy_name=strategy_name)
    if not query:
        return []
    retrieved = kb_client.retrieve(query, k)
    return [provenance_to_citation(item) for item in retrieved.items]


def _dedup_citations(citations: list[KbCitation]) -> list[KbCitation]:
    """Drop duplicate ``(source, locator)`` pairs while preserving order.

    Caller-supplied citations come from the hypothesis loop's ``kb_cites``
    field; the rationale's own retrieval can return the same chunks. The
    ledger record should not double-cite the same locator.
    """
    seen: set[tuple[str, str]] = set()
    out: list[KbCitation] = []
    for c in citations:
        key = (c.source, c.locator)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def build_rationale_inputs(
    result: OptimizerResult,
    *,
    citations: list[KbCitation] | None = None,
    kb_client: KbClient | None = None,
    strategy_name: str | None = None,
    kb_top_k: int = _DEFAULT_KB_TOP_K,
) -> RationaleInputs:
    """Compute the surface features that ground the rationale.

    Features:

    - For each numeric parameter, the Pearson correlation between its
      values across accepted trials and the trial scores. Reported when
      |corr| ≥ 0.2 and ≥ 4 trials contribute, to avoid noise-driven claims.
    - The score-plateau ratio: fraction of accepted trials scoring within
      5 % of the best trial. A high ratio is a positive signal (broad
      optimum, robust to misspecification); a low ratio warrants a hedged
      claim ("narrow optimum — sensitive to parameter drift").
    - The acceptance ratio: accepted / total trials. Low acceptance means
      the constraint surface is tight, which the LLM should mention.
    """
    caller_cites = list(citations) if citations else []
    kb_cites: list[KbCitation] = []
    if kb_client is not None:
        kb_cites = _retrieve_kb_citations(
            result,
            kb_client=kb_client,
            strategy_name=strategy_name,
            k=kb_top_k,
        )
    merged_cites = _dedup_citations([*caller_cites, *kb_cites])

    if result.best is None:
        return RationaleInputs(
            best_params={},
            best_score=float("nan"),
            surface_features=[
                SurfaceFeature(
                    description=(
                        f"all {len(result.trials)} candidates rejected — no "
                        "parameter set passed the objective constraints"
                    ),
                    weight=1.0,
                )
            ],
            citations=merged_cites,
            accepted_trial_count=0,
            rejected_trial_count=result.rejected_count,
        )

    accepted: list[Trial] = [t for t in result.trials if t.accepted]
    features: list[SurfaceFeature] = []

    # Parameter-vs-score correlations.
    if len(accepted) >= _MIN_TRIALS_FOR_CORRELATION:
        numeric_param_names = _numeric_param_names(accepted)
        for name in numeric_param_names:
            xs = [float(t.params[name]) for t in accepted]
            ys = [t.outcome.score for t in accepted]
            corr = _pearson(xs, ys)
            if corr is not None and abs(corr) >= _CORRELATION_REPORT_THRESHOLD:
                direction = "with" if corr > 0 else "against"
                features.append(
                    SurfaceFeature(
                        description=(
                            f"{name} correlates {direction} the primary score "
                            f"({corr:+.2f}) across {len(accepted)} accepted trials"
                        ),
                        weight=abs(corr),
                    )
                )

    # Plateau / spread.
    if accepted:
        best_score = result.best.outcome.score
        within_5pct = sum(
            1
            for t in accepted
            if abs(t.outcome.score - best_score) <= 0.05 * abs(best_score) + 1e-9
        )
        ratio = within_5pct / len(accepted)
        if ratio >= _PLATEAU_RATIO_THRESHOLD:
            features.append(
                SurfaceFeature(
                    description=(
                        f"{within_5pct} of {len(accepted)} accepted trials score within "
                        "5% of the best — broad plateau, robust parameter selection"
                    ),
                    weight=0.8,
                )
            )
        else:
            features.append(
                SurfaceFeature(
                    description=(
                        f"only {within_5pct} of {len(accepted)} accepted trials are within "
                        "5% of the best — narrow optimum, sensitive to parameter drift"
                    ),
                    weight=0.8,
                )
            )

    # Acceptance ratio.
    total = len(result.trials)
    if total > 0:
        accept_ratio = len(accepted) / total
        if accept_ratio < _TIGHT_FEASIBLE_REGION_THRESHOLD:
            features.append(
                SurfaceFeature(
                    description=(
                        f"{result.rejected_count} of {total} candidates rejected by the "
                        "objective constraints — the feasible region is tight"
                    ),
                    weight=0.6,
                )
            )

    return RationaleInputs(
        best_params=dict(result.best.params),
        best_score=result.best.outcome.score,
        surface_features=features,
        citations=merged_cites,
        accepted_trial_count=len(accepted),
        rejected_trial_count=result.rejected_count,
    )


def _numeric_param_names(trials: list[Trial]) -> list[str]:
    if not trials:
        return []
    names: list[str] = []
    sample = trials[0].params
    for name, value in sample.items():
        if isinstance(value, bool):
            # bool is a subclass of int; treat it as categorical, not numeric.
            continue
        if isinstance(value, int | float):
            names.append(name)
    return names


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < _MIN_OBS_FOR_PEARSON:
        return None
    try:
        sx = statistics.pstdev(xs)
        sy = statistics.pstdev(ys)
    except statistics.StatisticsError:
        return None
    if sx == 0 or sy == 0:
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / len(xs)
    corr = cov / (sx * sy)
    if math.isnan(corr) or math.isinf(corr):
        return None
    return corr


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class TemplateRationaleClient:
    """Deterministic, no-LLM rationale renderer.

    Produces a sentence-per-feature summary plus citation tags. Used by
    tests and as a fallback when no reasoning client is configured. The
    output is replayable byte-identically given the same inputs, which is
    why the optimizer's ledger entry can carry the rationale as evidence.
    """

    def write_rationale(self, inputs: RationaleInputs) -> str:
        if not inputs.best_params:
            return (
                "Optimizer returned no accepted parameter set. "
                f"{inputs.rejected_trial_count} candidates rejected by the objective."
            )
        lines: list[str] = []
        param_summary = ", ".join(
            f"{k}={_format_value(v)}" for k, v in sorted(inputs.best_params.items())
        )
        lines.append(
            f"Selected parameters: {param_summary} (score {inputs.best_score:.4f}, "
            f"{inputs.accepted_trial_count} accepted of "
            f"{inputs.accepted_trial_count + inputs.rejected_trial_count} candidates)."
        )
        for feature in sorted(inputs.surface_features, key=lambda f: -f.weight):
            lines.append(f"- {feature.description}.")
        if inputs.citations:
            cite_text = "; ".join(f"[{c.source}: {c.locator}]" for c in inputs.citations)
            lines.append(f"Supporting evidence from the knowledge base: {cite_text}.")
        return "\n".join(lines)


def _format_value(v: object) -> str:
    if isinstance(v, float):
        if math.isfinite(v):
            return f"{v:.4g}"
        return str(v)
    return str(v)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_rationale(  # noqa: PLR0913 — distinct knobs; collapsing them is worse
    result: OptimizerResult,
    *,
    citations: list[KbCitation] | None = None,
    client: RationaleClient | None = None,
    kb_client: KbClient | None = None,
    strategy_name: str | None = None,
    kb_top_k: int = _DEFAULT_KB_TOP_K,
) -> str:
    """Compute surface features, call the rationale client, return text.

    Two citation sources are merged into the rationale (deduplicated on
    ``(source, locator)``):

    - ``citations`` — already-attached cites from the hypothesis loop's
      ``state.kb_cites``, threaded through when the optimizer is invoked
      as part of a tested hypothesis.
    - ``kb_client.retrieve(query, kb_top_k)`` — the rationale generator's
      own KB consultation, required by
      `param-optimizer::optimized-output-and-rationale` ("rationale
      generator MUST consult the Knowledge Base in addition to optimizer
      output"). The query is composed from ``strategy_name`` (when
      supplied) plus the best-trial parameter names. Skip the second
      retrieval by passing ``kb_client=None``.

    When ``client`` is ``None``, the deterministic template client is used
    — convenient for tests and offline runs.
    """
    inputs = build_rationale_inputs(
        result,
        citations=citations,
        kb_client=kb_client,
        strategy_name=strategy_name,
        kb_top_k=kb_top_k,
    )
    chosen = client if client is not None else TemplateRationaleClient()
    return chosen.write_rationale(inputs)


__all__ = [
    "RationaleClient",
    "RationaleInputs",
    "SurfaceFeature",
    "TemplateRationaleClient",
    "build_rationale_inputs",
    "generate_rationale",
]
