"""Python wrapper around the PyO3 `objectives` module.

Surface:
- :func:`validate_spec` — returns a `ValidationReport`.
- :func:`evaluate_spec` — returns an `EvaluationOutcome` for a metrics dict.
- :func:`engine_metrics` — list of canonical engine metric names that
  primaries/secondaries may reference.

The objective spec and metrics dicts travel as JSON-serializable mappings
so the orchestrator can hand the LLM-emitted YAML/JSON straight through.
"""

from __future__ import annotations

import json
from typing import Any

from ._native_shim import require_native
from .types import EvaluationOutcome, ValidationReport


def validate_spec(spec: dict[str, Any]) -> ValidationReport:
    """Validate an objective spec dict; never raises on invalid specs."""
    native = require_native()
    raw: str = native.objectives.validate_spec(json.dumps(spec))
    return ValidationReport.model_validate_json(raw)


def evaluate_spec(spec: dict[str, Any], metrics: dict[str, Any]) -> EvaluationOutcome:
    """Score `metrics` against `spec`; returns an `EvaluationOutcome`.

    The Rust evaluator emits ``score: null`` when a hard constraint is
    violated (no primary score is computable). Coerce to ``-inf`` so the
    Python side can rank trials uniformly without special-casing
    rejection at every call site.
    """
    native = require_native()
    raw: str = native.objectives.evaluate_spec(json.dumps(spec), json.dumps(metrics))
    payload: dict[str, Any] = json.loads(raw)
    if payload.get("score") is None:
        payload["score"] = float("-inf")
    return EvaluationOutcome.model_validate(payload)


def engine_metrics() -> list[str]:
    """Canonical engine metric names."""
    native = require_native()
    raw: str = native.objectives.engine_metrics()
    parsed: list[str] = json.loads(raw)
    return parsed


__all__ = ["engine_metrics", "evaluate_spec", "validate_spec"]
