"""Shared one-shot driver for methods that emit all candidates up-front.

Grid, random, and Sobol all produce their full candidate list before
running any evaluations — they share the same engine-IO loop (pack a
batch, submit, score per-run, persist trial rows, track the best).
This module factors that loop out so each method file only owns its
candidate generator.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .base import FoldSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner
    from ..optimizer import ParamSet


def search_one_shot(ctx: FoldSearchContext, candidates: Iterable[ParamSet]) -> FoldWinner:
    """Pack every candidate into one engine batch and pick the best accepted."""
    from ..optimization_runner import (
        FoldWinner,
        TrialRow,
        _build_run,
        _fallback_winner,
        _failed_outcome,
        _pack_batch,
        _reject_reason,
        _score,
        _submit_and_collect,
    )

    cand_list = list(candidates)
    spec = _pack_batch(
        experiment=ctx.experiment,
        dataset_manifest=ctx.dataset_manifest,
        runs=[_build_run(ctx.template, c, ctx.fold.train) for c in cand_list],
        parallelism=ctx.parallelism,
    )
    entries = _submit_and_collect(
        ctx.engine,
        artifact_path=ctx.artifact_path,
        bars=ctx.bars,
        spec=spec,
        dataset_manifest=ctx.dataset_manifest,
        poll_interval_secs=ctx.poll_interval_secs,
    )
    best_params: ParamSet | None = None
    best_metrics = None
    best_score = float("-inf")
    for params, entry in zip(cand_list, entries, strict=True):
        outcome = _score(ctx.objective, entry.metrics) if entry.ok else _failed_outcome(entry.error)
        accepted = outcome.accepted
        row = TrialRow(
            trial_id=next(ctx.trial_counter),
            round=0,
            phase=f"train_fold_{ctx.fold_index}",
            fold_index=ctx.fold_index,
            params=params,
            seed=ctx.template.seed,
            metrics=entry.metrics,
            score=outcome.score,
            accepted=accepted,
            reject_reason="" if accepted else _reject_reason(entry, outcome),
            wall_secs=entry.wall_secs,
        )
        ctx.trial_rows.append(row)
        if ctx.persist_writer is not None:
            ctx.persist_writer.emit_row(row)
        if accepted and outcome.score > best_score:
            best_score = outcome.score
            best_params = params
            best_metrics = entry.metrics
    if ctx.persist_writer is not None:
        ctx.persist_writer.flush()
    if best_params is None or best_metrics is None:
        return _fallback_winner(ctx.trial_rows, ctx.fold_index)
    return FoldWinner(
        fold_index=ctx.fold_index,
        params=best_params,
        train_metrics=best_metrics,
        train_score=best_score,
    )


__all__ = ["search_one_shot"]
