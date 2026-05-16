"""``method: differential_evolution`` — Storn & Price DE via scipy."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ..experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from ..experiment_spec import (
    DEKnobs,
    OptimizeBlock,
)
from ..optimizer import (
    de_bounds_and_integrality,
    de_project_individual,
    de_resolve_popsize,
    de_sobol_init,
)
from .base import FoldSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner


class DifferentialEvolutionSearch:
    """One generation = one packed engine batch via scipy's vectorized DE."""

    name = "differential_evolution"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        from scipy.optimize import differential_evolution

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

        knobs = (
            ctx.optim.differential_evolution
            if ctx.optim.differential_evolution is not None
            else DEKnobs()
        )
        keys, bounds, integrality = de_bounds_and_integrality(ctx.space)
        if not keys:
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        popsize = de_resolve_popsize(knobs.popsize, len(keys))
        init_array = (
            de_sobol_init(ctx.space, keys, popsize, seed=ctx.optim.seed)
            if knobs.init == "sobol"
            else knobs.init
        )

        gen = [0]
        best_params = None
        best_metrics = None
        best_score = [float("-inf")]

        def evaluate_population(population: Any) -> Any:  # noqa: ANN401
            import numpy as np

            nonlocal best_params, best_metrics
            cols = population.shape[1]
            candidates = [
                de_project_individual(population[:, i], keys, integrality) for i in range(cols)
            ]
            g = gen[0]
            gen[0] += 1
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
            scores = np.zeros(cols, dtype=float)
            for i, (params, entry) in enumerate(zip(candidates, entries, strict=True)):
                outcome = (
                    _score(ctx.objective, entry.metrics)
                    if entry.ok
                    else _failed_outcome(entry.error)
                )
                accepted = outcome.accepted
                row = TrialRow(
                    trial_id=next(ctx.trial_counter),
                    round=g,
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
                if accepted and outcome.score > best_score[0]:
                    best_score[0] = outcome.score
                    best_params = params
                    best_metrics = entry.metrics
                scores[i] = -outcome.score if math.isfinite(outcome.score) else 1e18
            if ctx.persist_writer is not None:
                ctx.persist_writer.flush()
            return scores

        differential_evolution(
            evaluate_population,
            bounds=bounds,
            maxiter=knobs.n_generations,
            popsize=popsize,
            strategy=knobs.strategy,
            mutation=(knobs.mutation_low, knobs.mutation_high),
            recombination=knobs.crossover,
            seed=ctx.optim.seed,
            init=init_array,
            integrality=integrality,
            tol=knobs.tol,
            vectorized=True,
            polish=False,
        )

        if best_params is None or best_metrics is None:
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        return FoldWinner(
            fold_index=ctx.fold_index,
            params=best_params,
            train_metrics=best_metrics,
            train_score=best_score[0],
        )

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        knobs = (
            optim.differential_evolution if optim.differential_evolution is not None else DEKnobs()
        )
        n_dims = sum(1 for p in optim.space.values() if not isinstance(p, SpecChoiceParam))
        pop = de_resolve_popsize(knobs.popsize, n_dims)
        return pop * knobs.n_generations * folds_count + folds_count * folds_count


__all__ = ["DifferentialEvolutionSearch"]
