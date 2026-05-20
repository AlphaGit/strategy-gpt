"""Consolidated reject-reason taxonomy for the hypothesis loop.

Centralizes the structural reject kinds emitted by validators, the
mechanical gate, the verdict-critique node, and the tester's
``attempt_with_optimize`` surface. Existing modules (validation.py,
repair.py) historically used bare strings; this module gives them a
single canonical enum + a structured-rationale builder so the
``DecisionRecord.evidence`` payload is uniform regardless of which
check fired.

The full taxonomy from ``tester::expanded-reject-reason-taxonomy``:

- ``reject_format`` — LLM emission failed the markdown parse contract.
- ``reject_build`` — cargo build failed.
- ``reject_lint`` — build-pipeline lint check failed.
- ``reject_schema`` — ``param_intent`` references parameters absent from
  the compiled artifact's declared schema, or violates bounds.
- ``reject_smoke`` — smoke backtest failed (panic / no trades / sanity).
- ``reject_noise`` — candidate score did not clear the variance-aware
  score floor.
- ``reject_variance`` — per-fold CV exceeded threshold.
- ``reject_verdict`` — LLM verdict-critique rejected after the
  mechanical gate passed.
- ``reject_deps`` — Cargo.toml declared a crate outside the whitelist.
- ``exhausted_repair_budget`` — stage burned through ``K_repair``
  attempts without producing a valid emission.

The ``RejectKind`` value strings are stable across the codebase; the
existing string-typed call sites in :mod:`strategy_gpt.validation` and
:mod:`strategy_gpt.repair` already use these literals, so importing
this enum is opt-in and backwards-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RejectKind(StrEnum):
    """Canonical reject taxonomy for the hypothesis loop.

    Members mirror the spec's wording exactly; downstream consumers (the
    repair loop, the ledger evidence builder) compare against these
    values as strings.
    """

    OK = "ok"
    REJECT_FORMAT = "reject_format"
    REJECT_BUILD = "reject_build"
    REJECT_LINT = "reject_lint"
    REJECT_SCHEMA = "reject_schema"
    REJECT_SMOKE = "reject_smoke"
    REJECT_NOISE = "reject_noise"
    REJECT_VARIANCE = "reject_variance"
    REJECT_VERDICT = "reject_verdict"
    REJECT_DEPS = "reject_deps"
    EXHAUSTED_REPAIR_BUDGET = "exhausted_repair_budget"


_REPAIRABLE: frozenset[RejectKind] = frozenset(
    {
        RejectKind.REJECT_FORMAT,
        RejectKind.REJECT_BUILD,
        RejectKind.REJECT_LINT,
        RejectKind.REJECT_SCHEMA,
        RejectKind.REJECT_SMOKE,
        RejectKind.REJECT_DEPS,
    }
)


def is_repairable(kind: RejectKind | str) -> bool:
    """True if ``kind`` is a structural failure the repair loop may retry.

    The spec is explicit: mechanical-gate failures
    (``reject_noise`` / ``reject_variance``) and ``reject_verdict`` MUST
    NOT trigger repair attempts. Callers use this predicate to gate the
    decision rather than re-encoding the rule at each call site.
    """
    if isinstance(kind, str):
        try:
            kind = RejectKind(kind)
        except ValueError:
            return False
    return kind in _REPAIRABLE


@dataclass(frozen=True, slots=True)
class RejectRationale:
    """Structured rationale persisted with every reject decision.

    ``summary`` is the short, ledger-facing reason (e.g. ``"candidate
    score did not clear variance-aware floor"``). ``evidence`` carries
    the structured detail the spec demands per kind — for
    ``reject_noise`` that's ``score`` / ``baseline_score`` / ``sigma`` /
    ``k``; for ``reject_schema`` that's the offending parameter list;
    for ``reject_build`` that's the first three rustc errors.

    Frozen so a rationale can be hashed for dedup or embedded in a
    parquet row as JSON.
    """

    kind: RejectKind
    summary: str
    evidence: dict[str, Any]

    def to_evidence_dict(self) -> dict[str, Any]:
        """Project to the dict shape expected by
        :class:`DecisionRecord.evidence`."""
        return {
            "reject_kind": self.kind.value,
            "summary": self.summary,
            **self.evidence,
        }


# ---------------------------------------------------------------------------
# Builders — keep the per-kind contract in one place so call sites do
# not drift on which evidence keys must accompany which kind.
# ---------------------------------------------------------------------------


def noise_rationale(
    *,
    score: float,
    baseline_score: float,
    sigma_combined: float,
    k: float,
) -> RejectRationale:
    """``reject_noise`` evidence (`tester::noise-rejection-records-the-gap`)."""
    return RejectRationale(
        kind=RejectKind.REJECT_NOISE,
        summary=(
            f"candidate-baseline delta {score - baseline_score:+.4f} "
            f"failed to exceed {k:.2f} * sigma_combined ({sigma_combined:.4f})"
        ),
        evidence={
            "score": score,
            "baseline_score": baseline_score,
            "sigma_combined": sigma_combined,
            "k": k,
            "delta": score - baseline_score,
        },
    )


def variance_rationale(*, fold_cv: float, threshold: float) -> RejectRationale:
    """``reject_variance`` evidence."""
    return RejectRationale(
        kind=RejectKind.REJECT_VARIANCE,
        summary=f"per-fold CV {fold_cv:.4f} exceeded threshold {threshold:.4f}",
        evidence={"fold_cv": fold_cv, "threshold": threshold},
    )


def schema_rationale(
    *,
    missing_added: list[str] | None = None,
    leaked_removed: list[str] | None = None,
    bound_violations: list[str] | None = None,
) -> RejectRationale:
    """``reject_schema`` evidence (`tester::schema-mismatch-rejection`)."""
    parts: list[str] = []
    if missing_added:
        parts.append(f"missing added params: {sorted(missing_added)}")
    if leaked_removed:
        parts.append(f"removed params still declared: {sorted(leaked_removed)}")
    if bound_violations:
        parts.append(f"bound violations: {bound_violations}")
    summary = "; ".join(parts) or "param_intent does not match declared schema"
    return RejectRationale(
        kind=RejectKind.REJECT_SCHEMA,
        summary=summary,
        evidence={
            "missing_added": list(missing_added or []),
            "leaked_removed": list(leaked_removed or []),
            "bound_violations": list(bound_violations or []),
        },
    )


def verdict_rationale(
    *,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> RejectRationale:
    """``reject_verdict`` evidence — LLM-driven rejection after mechanical
    gate passed. The LLM's free-form rejection rationale lands in
    ``summary``; ``detail`` captures the structured fields the
    verdict-critique node already computed (side-effect deltas,
    complexity diff, etc.)."""
    return RejectRationale(
        kind=RejectKind.REJECT_VERDICT,
        summary=reason,
        evidence=dict(detail or {}),
    )


def deps_rationale(*, unlisted_crates: list[str]) -> RejectRationale:
    """``reject_deps`` evidence."""
    return RejectRationale(
        kind=RejectKind.REJECT_DEPS,
        summary=(
            f"Cargo.toml declared crate(s) outside the allowed whitelist: {sorted(unlisted_crates)}"
        ),
        evidence={"unlisted_crates": sorted(unlisted_crates)},
    )


def format_rationale(*, section: str, detail: str) -> RejectRationale:
    """``reject_format`` evidence — wraps a :class:`markdown_io.ParseError`."""
    return RejectRationale(
        kind=RejectKind.REJECT_FORMAT,
        summary=f"{section}: {detail}" if section else detail,
        evidence={"section": section, "detail": detail},
    )


def build_rationale(*, error_kind: str, message: str) -> RejectRationale:
    """``reject_build`` evidence."""
    return RejectRationale(
        kind=RejectKind.REJECT_BUILD,
        summary=f"build failed ({error_kind})",
        evidence={"error_kind": error_kind, "message": message},
    )


def lint_rationale(
    *, source_violations: list[str], manifest_violations: list[str]
) -> RejectRationale:
    """``reject_lint`` evidence."""
    return RejectRationale(
        kind=RejectKind.REJECT_LINT,
        summary=(
            f"lint failed: {len(source_violations)} source, "
            f"{len(manifest_violations)} manifest violation(s)"
        ),
        evidence={
            "source_violations": list(source_violations),
            "manifest_violations": list(manifest_violations),
        },
    )


def smoke_rationale(
    *, kind: str, message: str, extras: dict[str, Any] | None = None
) -> RejectRationale:
    """``reject_smoke`` evidence."""
    return RejectRationale(
        kind=RejectKind.REJECT_SMOKE,
        summary=f"smoke {kind}: {message}" if message else f"smoke {kind}",
        evidence={"smoke_kind": kind, "message": message, **(extras or {})},
    )


__all__ = [
    "RejectKind",
    "RejectRationale",
    "build_rationale",
    "deps_rationale",
    "format_rationale",
    "is_repairable",
    "lint_rationale",
    "noise_rationale",
    "schema_rationale",
    "smoke_rationale",
    "variance_rationale",
    "verdict_rationale",
]
