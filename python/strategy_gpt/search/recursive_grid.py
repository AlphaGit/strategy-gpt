"""``method: recursive_grid`` — round-wise box-shrinking grid."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..experiment_spec import OptimizeBlock, RecursiveGridKnobs
from ..optimizer import (
    RandomParam,
    RecursiveGridDriver,
    RecursiveGridSearcher,
    Trial,
)
from .base import FoldSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner


def _build_searcher(optim: OptimizeBlock, space: dict[str, RandomParam]) -> RecursiveGridSearcher:
    from ..optimization_runner import per_dim_resolutions

    knobs = optim.recursive_grid or RecursiveGridKnobs()
    return RecursiveGridSearcher(
        space=space,
        resolution=knobs.resolution,
        top_k=knobs.top_k,
        depth=knobs.depth,
        plateau_epsilon=knobs.plateau_epsilon,
        seed=optim.seed,
        per_dim_resolution=per_dim_resolutions(optim.space),
    )


class RecursiveGridSearch:
    """Each round packs that round's candidate set as one engine batch."""

    name = "recursive_grid"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
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

        searcher = _build_searcher(ctx.optim, ctx.space)
        driver = RecursiveGridDriver(searcher, salt=ctx.fold_index * 1_000_003)
        best_params = None
        best_metrics = None
        best_score = float("-inf")
        round_idx = 0
        while not driver.done:
            candidates = driver.candidates()
            if not candidates:
                break
            spec = _pack_batch(
                experiment=ctx.experiment,
                dataset_manifest=ctx.dataset_manifest,
                runs=[_build_run(ctx.template, c, ctx.fold.train) for c in candidates],
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
            round_trials: list[Trial] = []
            for params, entry in zip(candidates, entries, strict=True):
                outcome = (
                    _score(ctx.objective, entry.metrics)
                    if entry.ok
                    else _failed_outcome(entry.error)
                )
                accepted = outcome.accepted
                t = Trial(params=params, metrics=entry.metrics, outcome=outcome, accepted=accepted)
                round_trials.append(t)
                row = TrialRow(
                    trial_id=next(ctx.trial_counter),
                    round=round_idx,
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
            driver.observe(round_trials)
            if ctx.persist_writer is not None:
                ctx.persist_writer.flush()
            round_idx += 1
        if best_params is None or best_metrics is None:
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        return FoldWinner(
            fold_index=ctx.fold_index,
            params=best_params,
            train_metrics=best_metrics,
            train_score=best_score,
        )

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        from ..optimization_runner import per_dim_resolutions

        knobs = optim.recursive_grid or RecursiveGridKnobs()
        dims = len(optim.space)
        per_dim_res = per_dim_resolutions(optim.space)
        runs_per_round = 1
        for name in optim.space:
            runs_per_round *= int(per_dim_res.get(name, knobs.resolution))
        if dims == 0:
            runs_per_round = 0
        total_train = runs_per_round * knobs.depth * folds_count
        return total_train + folds_count * folds_count


__all__ = ["RecursiveGridSearch"]
