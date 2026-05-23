"""User-facing rendering for the author dialog.

Centralizes the locked-in decisions panel and any other in-dialog UI so
the dialog driver and the CLI stay decoupled from presentation details.
"""

from __future__ import annotations

from typing import Any

_PANEL_TITLE = "Decisions locked in so far"
_SEPARATOR = "─" * 64
_FIELD_LABELS: dict[str, str] = {
    "crate_name": "name",
    "edit_mode_target": "edit-mode",
    "universe": "universe",
    "mechanism_summary": "mechanism",
    "param_sketch": "params",
    "smoke_spec": "smoke",
    "experiment_spec": "experiment",
}
_ORDERED_FIELDS: tuple[str, ...] = (
    "crate_name",
    "edit_mode_target",
    "universe",
    "mechanism_summary",
    "param_sketch",
    "smoke_spec",
    "experiment_spec",
)
_MAX_VALUE_CHARS = 80


def render_decisions_panel(projection: dict[str, Any]) -> str:
    """Return a banner-style summary of the current decision projection.

    Returns an empty string when the projection is empty so the caller
    can suppress an otherwise-empty banner without branching. Long-form
    values (multi-line, or wider than ``_MAX_VALUE_CHARS``) are collapsed
    to a head + ellipsis so the whole panel fits within roughly one
    screen.
    """
    if not projection:
        return ""
    lines: list[str] = [_SEPARATOR, _PANEL_TITLE, _SEPARATOR]
    label_width = max(len(_FIELD_LABELS.get(f, f)) for f in projection)
    for field_name in _ORDERED_FIELDS:
        if field_name not in projection:
            continue
        label = _FIELD_LABELS.get(field_name, field_name).ljust(label_width)
        value_str = _format_value(projection[field_name])
        lines.append(f"  {label}  {value_str}")
    lines.append(_SEPARATOR)
    return "\n".join(lines)


def _format_value(value: object) -> str:
    """Render a decision value as a single line, collapsing long content."""
    if isinstance(value, dict):
        rendered = ", ".join(f"{k}={_format_scalar(v)}" for k, v in value.items())
    elif isinstance(value, list):
        rendered = ", ".join(_format_scalar(v) for v in value)
    else:
        rendered = _format_scalar(value)
    rendered = rendered.replace("\n", " ").strip()
    if len(rendered) > _MAX_VALUE_CHARS:
        return rendered[: _MAX_VALUE_CHARS - 1] + "…"
    return rendered


def _format_scalar(value: object) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


__all__ = ["render_decisions_panel"]
