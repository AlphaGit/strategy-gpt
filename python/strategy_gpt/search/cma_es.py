"""``method: cma_es`` — Hansen 2016 CMA-ES via the ``cma`` package."""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from ..experiment_spec import (
    CmaEsKnobs,
    OptimizeBlock,
)
from ..optimizer import (
    cma_dedup_rate,
    cma_resolve_popsize,
    cma_unit_to_params,
    de_bounds_and_integrality,
)
from .base import FoldSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner


_DEDUP_WARN_THRESHOLD = 0.30
_REJECT_MAX_ATTEMPTS = 16


def _unit_out_of_bounds(unit: Sequence[float]) -> bool:
    return any(u < 0.0 or u > 1.0 for u in unit)


class CmaEsSearch:
    """Unit-cube rescaled CMA-ES; each generation packs as one engine batch."""

    name = "cma_es"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        import cma

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

        knobs = ctx.optim.cma_es if ctx.optim.cma_es is not None else CmaEsKnobs()
        if knobs.restart_strategy != "null":
            msg = (
                f"cma_es: restart_strategy={knobs.restart_strategy!r} is not yet "
                "implemented; only 'null' is supported. File an issue if you need "
                "IPOP/BIPOP restarts."
            )
            raise NotImplementedError(msg)

        keys, bounds_pairs, integrality = de_bounds_and_integrality(ctx.space)
        if not keys:
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        n_dims = len(keys)
        popsize = cma_resolve_popsize(knobs.popsize, n_dims)

        x0 = [0.5] * n_dims
        options: dict[str, Any] = {
            "popsize": popsize,
            "seed": ctx.optim.seed + ctx.fold_index * 7919 + 1,
            "bounds": [[0.0] * n_dims, [1.0] * n_dims],
            "verbose": -9,
            "maxiter": knobs.n_generations,
        }
        es = cma.CMAEvolutionStrategy(x0, knobs.sigma0, options)

        best_params = None
        best_metrics = None
        best_score = float("-inf")
        gen = 0
        while not es.stop() and gen < knobs.n_generations:
            raw_units = es.ask()
            if knobs.bounds == "reject":
                for i in range(len(raw_units)):
                    attempt = 0
                    while _unit_out_of_bounds(raw_units[i]) and attempt < _REJECT_MAX_ATTEMPTS:
                        raw_units[i] = es.ask(1)[0]
                        attempt += 1
            candidates = [
                cma_unit_to_params(u, keys, bounds_pairs, integrality, knobs.bounds)
                for u in raw_units
            ]
            dup_rate = cma_dedup_rate(candidates)
            if any(integrality) and dup_rate > _DEDUP_WARN_THRESHOLD:
                warnings.warn(
                    f"cma_es fold {ctx.fold_index} gen {gen}: integer dedup rate "
                    f"{dup_rate:.2%} exceeds 30%; inflating sigma0 1.5x for this fold.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                es.sigma = es.sigma * 1.5
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
            neg_scores: list[float] = []
            for params, entry in zip(candidates, entries, strict=True):
                outcome = (
                    _score(ctx.objective, entry.metrics)
                    if entry.ok
                    else _failed_outcome(entry.error)
                )
                accepted = outcome.accepted
                row = TrialRow(
                    trial_id=next(ctx.trial_counter),
                    round=gen,
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
                neg_scores.append(-outcome.score if math.isfinite(outcome.score) else 1e18)
            es.tell(raw_units, neg_scores)
            if ctx.persist_writer is not None:
                ctx.persist_writer.flush()
            gen += 1

        if best_params is None or best_metrics is None:
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        return FoldWinner(
            fold_index=ctx.fold_index,
            params=best_params,
            train_metrics=best_metrics,
            train_score=best_score,
        )

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        knobs = optim.cma_es if optim.cma_es is not None else CmaEsKnobs()
        n_dims = sum(1 for p in optim.space.values() if not isinstance(p, SpecChoiceParam))
        pop = cma_resolve_popsize(knobs.popsize, n_dims)
        return pop * knobs.n_generations * folds_count + folds_count * folds_count


__all__ = ["CmaEsSearch"]
