"""Centralized stderr progress for ``strategy-gpt optimize``.

Wraps the optimization persist-writer protocol so every search method's
per-trial :meth:`emit_row` is observed in one place. Algorithms in
``search/*.py`` and the orchestrator in :mod:`optimization_runner`
remain progress-unaware — adding a new search method does not require
touching any UI code.

The renderer prints to stderr only (stdout stays reserved for command
results), reads everything it needs from :class:`TrialRow` fields plus
the writer-protocol ``start`` / ``finish`` calls, and is suppressed by
``--quiet`` / ``--json``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import typer

from .experiment_spec import ExperimentSpec
from .folds import FoldRange
from .optimization_runner import OptimizationResult, TrialRow

_CROSS_PHASE_PREFIX = "final_cross_"
_PARAM_SUMMARY_MAX_CHARS = 80


def _primary_metric_name(objective: Mapping[str, Any]) -> str:
    primary = objective.get("primary")
    if isinstance(primary, Mapping):
        name = primary.get("metric")
        if isinstance(name, str):
            return name
    return "sharpe"


def _fmt_num_no_sep(value: float) -> str:
    """Same shape as :func:`_fmt_num` but without thousands separators.

    Parameter values often live in tight ranges (`threshold=0.5`,
    `w_rsi=-0.42`) and embed in `params={ k=v,k=v }` strings; a
    thousands separator on the integer side would collide with the
    `,` already used as the param delimiter and make the line
    visually noisier than the underlying values warrant.
    """
    import math  # noqa: PLC0415 — keep import local; only used here

    v = float(value)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if abs(v) < _INT_DISPLAY_MAX_MAGNITUDE and v == round(v):
        return f"{int(v)}"
    return f"{v:.4f}"


def _short(v: object) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return _fmt_num_no_sep(v)
    if isinstance(v, int):
        return f"{v}"
    return str(v)


def _fmt_params(params: Mapping[str, Any], *, max_chars: int = _PARAM_SUMMARY_MAX_CHARS) -> str:
    joined = ",".join(f"{k}={_short(v)}" for k, v in params.items())
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 1] + "…"


def _primary_value(metrics: Mapping[str, Any], primary: str) -> float | None:
    v = metrics.get(primary)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


# Above this magnitude `int(v)` and `v == round(v)` are unreliable because the
# float can't represent unit increments — fall back to the decimal format so
# numbers like `f64::MAX` (1.8e308) don't crash through the integer branch.
_INT_DISPLAY_MAX_MAGNITUDE = 1e15


def _fmt_num(value: float) -> str:
    """Format a number with thousands separator.

    Integer-valued numbers (`14.0`, `9_557_640.0`, raw `int`s) render
    without a decimal point — `14`, `9,557,640` — because counts (trade
    counts, bar counts, volumes) read worse with trailing `.0000`.
    Genuinely fractional values render with up to 4 decimals,
    `1,234.5678`.
    """
    import math  # noqa: PLC0415 — keep import local; only used here

    v = float(value)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if abs(v) < _INT_DISPLAY_MAX_MAGNITUDE and v == round(v):
        return f"{int(v):,d}"
    return f"{v:,.4f}"


def _fmt_all_metrics(metrics: Mapping[str, Any]) -> str:
    """Format every numeric entry in `metrics` as `k=v`, comma-joined.

    Bool, None, and non-numeric values are skipped — they have no place
    on a score line. Order matches `metrics.items()` so callers control
    presentation by passing an ordered dict if they care.
    """
    parts: list[str] = []
    for k, v in metrics.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        parts.append(f"{k}={_fmt_num(v)}")
    return ", ".join(parts)


class _InnerWriter(Protocol):
    """The subset of ``_PersistWriter`` (in :mod:`optimization_runner`) we tee to."""

    def start(  # noqa: PLR0913 — mirror of the persist-writer wire shape.
        self,
        *,
        experiment: ExperimentSpec,
        objective: Mapping[str, Any],
        dataset_manifest: str,
        artifact_path: Path,
        opt_id: str,
        resolved_parallelism: int,
        seed: int,
        started_at: datetime,
        folds: Sequence[FoldRange],
    ) -> None: ...

    def emit_row(self, row: TrialRow) -> None: ...

    def flush(self) -> None: ...

    def finish(self, result: OptimizationResult) -> None: ...


class StderrProgressRenderer:
    """Human-readable per-phase progress + running metrics on stderr.

    State is per-phase: a running best score plus the most recently
    observed primary metric. The renderer prints only when a new best
    appears (or when a phase ends), keeping output bounded even for
    1000+ trial sweeps.
    """

    def __init__(self) -> None:
        self._primary: str = "sharpe"
        self._current_phase: str | None = None
        self._best_score: float = float("-inf")
        self._best_primary: float | None = None
        self._best_params: Mapping[str, Any] | None = None
        self._best_metrics: Mapping[str, Any] | None = None
        self._trials_in_phase: int = 0

    def on_start(  # noqa: PLR0913 — mirror of the persist-writer wire shape.
        self,
        *,
        experiment: ExperimentSpec,
        objective: Mapping[str, Any],
        opt_id: str,
        resolved_parallelism: int,
        seed: int,
        folds: Sequence[FoldRange],
    ) -> None:
        from .search import get as get_method  # noqa: PLC0415 — lazy to avoid import cycle.

        if experiment.optimize is None:
            return
        self._primary = _primary_metric_name(objective)
        method = experiment.optimize.method
        try:
            planned = get_method(method).planned_run_count(experiment.optimize, len(folds))
            planned_str = f"≈{planned}"
        except (ValueError, KeyError):
            planned_str = "?"
        self._echo(
            f"━━━ optimize {opt_id} | method={method} folds={len(folds)} "
            f"parallelism={resolved_parallelism} seed={seed} "
            f"planned={planned_str} trials | primary={self._primary} ━━━"
        )

    def on_trial(self, row: TrialRow) -> None:
        # Cross-validation rows are summarized in aggregate at on_finish; per-OOS-trial
        # lines would flood the log without adding signal beyond the aggregate.
        if row.phase.startswith(_CROSS_PHASE_PREFIX):
            return
        if row.phase != self._current_phase:
            self._end_phase()
            self._begin_phase(row.phase)
        self._trials_in_phase += 1
        if not (row.accepted and row.score > self._best_score):
            return
        self._best_score = row.score
        self._best_primary = _primary_value(row.metrics, self._primary)
        self._best_params = dict(row.params)
        self._best_metrics = dict(row.metrics)
        primary_disp = (
            f"{self._primary}={_fmt_num(self._best_primary)}"
            if self._best_primary is not None
            else f"{self._primary}=n/a"
        )
        self._echo(f"  trial #{row.trial_id} | params={{ {_fmt_params(row.params)} }}")
        self._echo(f"      {primary_disp} score={_fmt_num(row.score)} ✓ accepted (new best)")
        all_metrics = _fmt_all_metrics(row.metrics)
        if all_metrics:
            self._echo(f"      metrics: {{ {all_metrics} }}")

    def on_phase_flush(self) -> None:
        self._end_phase()

    def on_finish(self, result: OptimizationResult) -> None:
        self._end_phase()
        if result.cross_validation:
            self._echo(f"━━━ cross_validation ({len(result.cross_validation)} winner(s)) ━━━")
            # Pre-compute column widths so winner rows align vertically.
            rows: list[dict[str, str]] = []
            for i, cv in enumerate(result.cross_validation):
                primary_raw = cv.aggregate_metrics.get(self._primary)
                primary_str = (
                    _fmt_num(float(primary_raw))
                    if isinstance(primary_raw, (int, float)) and not isinstance(primary_raw, bool)
                    else "n/a"
                )
                tag = (
                    "✓ accepted"
                    if cv.aggregate_accepted
                    else f"✗ rejected: {cv.aggregate_reject_reason}"
                )
                rows.append(
                    {
                        "label": f"winner {i} (fold_{cv.fold_index})",
                        "primary": primary_str,
                        "score": _fmt_num(cv.aggregate_score),
                        "tag": tag,
                        "metrics": _fmt_all_metrics(cv.aggregate_metrics),
                    }
                )
            label_w = max(len(r["label"]) for r in rows)
            primary_w = max(len(r["primary"]) for r in rows)
            score_w = max(len(r["score"]) for r in rows)
            for r in rows:
                self._echo(
                    f"  {r['label']:<{label_w}}: "
                    f"{self._primary}={r['primary']:>{primary_w}} "
                    f"agg_score={r['score']:>{score_w}} {r['tag']}"
                )
                if r["metrics"]:
                    self._echo(f"      metrics: {{ {r['metrics']} }}")
        sel = result.selection
        if sel is not None:
            self._echo(f"━━━ selection verdict: {sel.status.value} ━━━")
        if result.final is not None:
            primary_raw = result.final.aggregate_metrics.get(self._primary)
            primary_disp = (
                f" {self._primary}={_fmt_num(float(primary_raw))}"
                if isinstance(primary_raw, (int, float)) and not isinstance(primary_raw, bool)
                else ""
            )
            self._echo(
                f"  final pick: fold_{result.final.fold_index}"
                f"{primary_disp} agg_score={_fmt_num(result.final.aggregate_score)}"
            )
            final_metrics = _fmt_all_metrics(result.final.aggregate_metrics)
            if final_metrics:
                self._echo(f"      metrics: {{ {final_metrics} }}")
        else:
            self._echo("  final pick: (none)")

    def _begin_phase(self, phase: str) -> None:
        self._current_phase = phase
        self._best_score = float("-inf")
        self._best_primary = None
        self._best_params = None
        self._best_metrics = None
        self._trials_in_phase = 0
        self._echo(f"━━━ {phase} ━━━")

    def _end_phase(self) -> None:
        if self._current_phase is None or self._trials_in_phase == 0:
            return
        if self._best_primary is None and self._best_score == float("-inf"):
            self._echo(
                f"  {self._current_phase}: {self._trials_in_phase} trial(s) — no accepted candidate"
            )
        else:
            primary_disp = (
                f"best {self._primary}={_fmt_num(self._best_primary)}"
                if self._best_primary is not None
                else f"best {self._primary}=n/a"
            )
            self._echo(
                f"  {self._current_phase}: {self._trials_in_phase:,d} trial(s) — "
                f"{primary_disp} score={_fmt_num(self._best_score)}"
            )
            if self._best_metrics is not None:
                all_metrics = _fmt_all_metrics(self._best_metrics)
                if all_metrics:
                    self._echo(f"      metrics: {{ {all_metrics} }}")
        self._trials_in_phase = 0

    @staticmethod
    def _echo(line: str) -> None:
        typer.echo(line, err=True)


class TeePersistWriter:
    """Forwards every persist-writer call to the real writer and the renderer.

    Satisfies the structural ``_PersistWriter`` protocol declared in
    :mod:`optimization_runner` — the orchestrator does not know it is
    talking to a tee.
    """

    def __init__(self, inner: _InnerWriter, reporter: StderrProgressRenderer) -> None:
        self._inner = inner
        self._reporter = reporter

    def start(  # noqa: PLR0913 — mirror the persist-writer wire shape.
        self,
        *,
        experiment: ExperimentSpec,
        objective: Mapping[str, Any],
        dataset_manifest: str,
        artifact_path: Path,
        opt_id: str,
        resolved_parallelism: int,
        seed: int,
        started_at: datetime,
        folds: Sequence[FoldRange],
    ) -> None:
        self._inner.start(
            experiment=experiment,
            objective=objective,
            dataset_manifest=dataset_manifest,
            artifact_path=artifact_path,
            opt_id=opt_id,
            resolved_parallelism=resolved_parallelism,
            seed=seed,
            started_at=started_at,
            folds=folds,
        )
        self._reporter.on_start(
            experiment=experiment,
            objective=objective,
            opt_id=opt_id,
            resolved_parallelism=resolved_parallelism,
            seed=seed,
            folds=folds,
        )

    def emit_row(self, row: TrialRow) -> None:
        self._inner.emit_row(row)
        self._reporter.on_trial(row)

    def flush(self) -> None:
        self._inner.flush()
        self._reporter.on_phase_flush()

    def finish(self, result: OptimizationResult) -> None:
        self._inner.finish(result)
        self._reporter.on_finish(result)


__all__ = ["StderrProgressRenderer", "TeePersistWriter"]
