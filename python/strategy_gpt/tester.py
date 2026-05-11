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

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hypothesis_loop import HypothesisCandidate

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


__all__ = [
    "ParamDiff",
    "ParamOnlyTranslationError",
    "TranslatedRun",
    "apply_param_diffs",
    "parse_param_only_change",
    "translate_param_only",
]
