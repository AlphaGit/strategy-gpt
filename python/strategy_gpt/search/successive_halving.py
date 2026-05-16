"""``method: successive_halving`` — multi-fidelity over fold count.

Reference: Jamieson & Talwalkar (2016), "Non-stochastic Best Arm
Identification and Hyperparameter Optimization", AISTATS.

Rung r evaluates the surviving candidates on ``budget_r`` folds (in
rolling fold-scheme order), ranks them by mean OOS score across those
folds, and keeps the top ``1/eta`` for rung ``r + 1`` at double the
fold budget. The final-rung survivors are returned as
:class:`FoldWinner` records and cross-validated by the orchestrator
across every fold's OOS slice like every other method's winners.

Trial rows produced by this method use the phase tag
``train_fold_<i>_rung_<r>`` so the parquet log makes the cascade
recoverable: candidates killed at rung r exist only in their own
folds' rung-r-and-earlier rows, never in later rungs.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from ..experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from ..experiment_spec import (
    OptimizeBlock,
    SuccessiveHalvingKnobs,
)
from ..optimizer import LhsSearcher, RandomParam, RandomSearcher, SobolSearcher
from .base import GlobalSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner
    from ..optimizer import ParamSet


def _initial_candidates(
    optim: OptimizeBlock, space: dict[str, RandomParam], knobs: SuccessiveHalvingKnobs
) -> list[ParamSet]:
    n = knobs.initial_candidates
    seed = knobs.init_seed
    if knobs.init_method == "sobol":
        return list(
            SobolSearcher(space=space, n_points=n, scramble=True, owen_seed=seed).candidates()
        )[:n]
    if knobs.init_method == "lhs":
        return list(LhsSearcher(space=space, n_points=n, seed=seed).candidates())
    return list(RandomSearcher(space=space, n_iter=n, seed=seed).candidates())


def _rung_budgets(initial_folds: int, eta: int, total_folds: int) -> list[int]:
    """Per-rung fold budgets: ``initial_folds, initial_folds*eta, ...`` capped at total."""
    budgets: list[int] = []
    b = max(1, initial_folds)
    while True:
        budgets.append(min(b, total_folds))
        if budgets[-1] >= total_folds:
            break
        b = b * eta
    return budgets


def _rung_survivor_counts(initial_candidates: int, eta: int, n_rungs: int) -> list[int]:
    """Per-rung survivor counts under the 1/eta halving cascade."""
    out = [initial_candidates]
    for _ in range(n_rungs - 1):
        keep = max(1, math.ceil(out[-1] / eta))
        out.append(keep)
    return out


class SuccessiveHalvingSearch:
    """Owns the cross-fold loop; per-rung evaluations as packed engine batches."""

    name = "successive_halving"

    def search_global(self, ctx: GlobalSearchContext) -> list[FoldWinner]:
        from ..optimization_runner import (
            FoldWinner,
            TrialRow,
            _build_run,
            _failed_outcome,
            _pack_batch,
            _reject_reason,
            _score,
            _submit_and_collect,
        )

        knobs = (
            ctx.optim.successive_halving
            if ctx.optim.successive_halving is not None
            else SuccessiveHalvingKnobs()
        )
        eta = knobs.eta
        total_folds = len(ctx.folds)
        if total_folds == 0:
            return []
        budgets = _rung_budgets(knobs.initial_folds, eta, total_folds)
        n_rungs = len(budgets)
        survivor_counts = _rung_survivor_counts(knobs.initial_candidates, eta, n_rungs)
        survivors: list[ParamSet] = _initial_candidates(ctx.optim, ctx.space, knobs)
        # Map candidate id -> best per-fold metrics observed so far. The
        # final rung promotes the surviving candidates' per-fold metrics
        # straight into FoldWinner records.
        cand_metrics: dict[int, dict[int, dict[str, float]]] = {id(c): {} for c in survivors}
        cand_train_score: dict[int, float] = {id(c): float("-inf") for c in survivors}

        for r, budget in enumerate(budgets):
            target_keep = survivor_counts[r]
            survivors = survivors[:target_keep]
            runs: list[dict[str, object]] = []
            plan: list[tuple[int, int]] = []  # (candidate_index, fold_index)
            for c_idx, params in enumerate(survivors):
                for f_idx in range(budget):
                    fold = ctx.folds[f_idx]
                    runs.append(_build_run(ctx.template, params, fold.train))
                    plan.append((c_idx, f_idx))
            spec = _pack_batch(
                experiment=ctx.experiment,
                dataset_manifest=ctx.dataset_manifest,
                runs=runs,
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
            per_cand_scores: dict[int, list[float]] = {i: [] for i in range(len(survivors))}
            for (c_idx, f_idx), entry in zip(plan, entries, strict=True):
                params = survivors[c_idx]
                outcome = (
                    _score(ctx.objective, entry.metrics)
                    if entry.ok
                    else _failed_outcome(entry.error)
                )
                accepted = outcome.accepted
                row = TrialRow(
                    trial_id=next(ctx.trial_counter),
                    round=r,
                    phase=f"train_fold_{f_idx}_rung_{r}",
                    fold_index=f_idx,
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
                cand_metrics[id(params)][f_idx] = entry.metrics
                if outcome.accepted:
                    per_cand_scores[c_idx].append(outcome.score)
            if ctx.persist_writer is not None:
                ctx.persist_writer.flush()
            # Mean score across the rung's budget folds (treat rejected
            # candidates as -inf so they fall to the back of the rank).
            mean_scores: list[tuple[int, float]] = []
            for i in range(len(survivors)):
                scores = per_cand_scores[i]
                if not scores:
                    mean_scores.append((i, float("-inf")))
                    continue
                m = statistics.fmean(scores)
                mean_scores.append((i, m))
                cand_train_score[id(survivors[i])] = m
            mean_scores.sort(key=lambda t: t[1], reverse=True)
            # Promote the top half / 1-in-eta for the next rung.
            keep = (
                survivor_counts[r + 1]
                if r + 1 < n_rungs
                else max(1, math.ceil(len(survivors) / eta))
            )
            kept_idx = [i for i, _ in mean_scores[:keep]]
            survivors = [survivors[i] for i in kept_idx]

        # Final rung's survivors -> FoldWinner per survivor.
        winners: list[FoldWinner] = []
        for i, params in enumerate(survivors):
            per_fold = cand_metrics[id(params)]
            # Use any recorded fold's metrics as the "train_metrics" — the
            # final-rung budget covers every fold, so the dict is full.
            train_metrics = per_fold.get(0, next(iter(per_fold.values()), {}))
            winners.append(
                FoldWinner(
                    fold_index=i,
                    params=dict(params),
                    train_metrics=dict(train_metrics),
                    train_score=cand_train_score.get(id(params), float("-inf")),
                )
            )
        return winners

    def search_fold(self, ctx: object) -> object:
        del ctx
        msg = (
            "successive_halving owns the cross-fold loop; the orchestrator "
            "must dispatch via search_global, not search_fold."
        )
        raise NotImplementedError(msg)

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        knobs = (
            optim.successive_halving
            if optim.successive_halving is not None
            else SuccessiveHalvingKnobs()
        )
        if any(isinstance(p, SpecChoiceParam) for p in optim.space.values()) and (
            knobs.init_method == "sobol"
        ):
            # The Sobol seeder rejects categoricals; ints/floats only.
            pass
        budgets = _rung_budgets(knobs.initial_folds, knobs.eta, folds_count)
        n_rungs = len(budgets)
        survivor_counts = _rung_survivor_counts(knobs.initial_candidates, knobs.eta, n_rungs)
        total = sum(
            survivors * budget for survivors, budget in zip(survivor_counts, budgets, strict=True)
        )
        return total + folds_count * folds_count


__all__ = ["SuccessiveHalvingSearch"]
