"""``method: lhs_polish`` — Latin Hypercube seed + Hooke-Jeeves polish.

Procedure (see ``2026-05-14-additional-search-methods/design.md §5``):

1. Generate ``lhs_n`` Latin Hypercube samples and evaluate them on the
   fold's train slice in one packed batch.
2. Take the top ``top_k_polish`` LHS points by score.
3. Run Hooke-Jeeves polish trajectories from each top-K point. Each
   polish step's ``2 * D`` axis-aligned probes (across all trajectories)
   pack as one engine batch — so a single step costs
   ``top_k_polish * 2 * D`` evaluations regardless of trajectory.
4. Fold winner = best across LHS evaluations + every polish trajectory.

Nelder-Mead polish is gated behind an experimental knob; it is fragile
on noisy objectives, so the documented default is Hooke-Jeeves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..experiment_spec import LhsPolishKnobs, OptimizeBlock
from ..optimizer import (
    LhsSearcher,
    cma_unit_to_params,
    de_bounds_and_integrality,
    hooke_jeeves_propose,
)
from .base import FoldSearchContext
from .one_shot import search_one_shot

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..optimization_runner import FoldWinner
    from ..optimizer import ParamSet


def _params_to_unit(
    params: ParamSet,
    keys: list[str],
    bounds_pairs: list[tuple[float, float]],
) -> list[float]:
    """Project a named param set back to a [0, 1]^D unit vector."""
    out: list[float] = []
    for i, k in enumerate(keys):
        lo, hi = bounds_pairs[i]
        v = float(params[k])
        if hi <= lo:
            out.append(0.0)
        else:
            out.append(min(1.0, max(0.0, (v - lo) / (hi - lo))))
    return out


class LhsPolishSearch:
    """LHS-seeded polish via Hooke-Jeeves; per-step probes pack as one batch."""

    name = "lhs_polish"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:  # noqa: PLR0912, PLR0915
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

        knobs = ctx.optim.lhs_polish if ctx.optim.lhs_polish is not None else LhsPolishKnobs()
        if knobs.polish != "hooke_jeeves":
            msg = (
                f"lhs_polish: polish={knobs.polish!r} requires the experimental "
                "Nelder-Mead flag, which is not enabled in this build."
            )
            raise NotImplementedError(msg)

        # ----- Step 1: LHS seed batch -----
        def _lhs_candidates() -> Iterable[ParamSet]:
            return LhsSearcher(
                space=ctx.space, n_points=knobs.lhs_n, seed=knobs.lhs_seed
            ).candidates()

        # search_one_shot writes rows tagged with phase=train_fold_<i> and
        # round=0; the polish phase below uses round>=1 to keep the
        # rung-style tagging recoverable.
        lhs_winner = search_one_shot(ctx, _lhs_candidates())

        keys, bounds_pairs, integrality = de_bounds_and_integrality(ctx.space)
        if not keys:
            return lhs_winner

        # Rank LHS evaluations to find top-K starting points. The orchestrator
        # already persisted them; we read them back from ctx.trial_rows.
        lhs_rows = [
            r
            for r in ctx.trial_rows
            if r.fold_index == ctx.fold_index and r.phase.startswith("train_fold_") and r.round == 0
        ]
        accepted_rows = sorted(
            (r for r in lhs_rows if r.accepted),
            key=lambda r: r.score,
            reverse=True,
        )[: knobs.top_k_polish]
        if not accepted_rows:
            return lhs_winner

        # ----- Step 2: parallel Hooke-Jeeves polish trajectories -----
        trajectories: list[dict[str, Any]] = [
            {
                "unit": _params_to_unit(dict(r.params), keys, bounds_pairs),
                "step": [knobs.initial_step] * len(keys),
                "best_score": r.score,
                "best_metrics": dict(r.metrics),
                "best_params": dict(r.params),
                "active": True,
            }
            for r in accepted_rows
        ]

        step_min = knobs.step_min
        n_dims = len(keys)
        polish_round = 1
        for _iter in range(knobs.max_polish_iters):
            active = [t for t in trajectories if t["active"]]
            if not active:
                break
            # Build the per-trajectory probe candidates as one packed batch.
            probe_cands: list[ParamSet] = []
            traj_index: list[tuple[int, int]] = []  # (traj idx in `trajectories`, probe idx)
            for traj in active:
                t_idx = trajectories.index(traj)
                probes = hooke_jeeves_propose(traj["unit"], traj["step"])
                for p_idx, u in enumerate(probes):
                    cands_params = cma_unit_to_params(u, keys, bounds_pairs, integrality, "clip")
                    probe_cands.append(cands_params)
                    traj_index.append((t_idx, p_idx))
            if not probe_cands:
                break
            spec = _pack_batch(
                experiment=ctx.experiment,
                dataset_manifest=ctx.dataset_manifest,
                runs=[_build_run(ctx.template, c, ctx.fold.train) for c in probe_cands],
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
            # Per-trajectory: did any probe improve?
            improved: dict[int, tuple[list[float], float, dict[str, float], ParamSet]] = {}
            for (t_idx, p_idx), params, entry in zip(traj_index, probe_cands, entries, strict=True):
                outcome = (
                    _score(ctx.objective, entry.metrics)
                    if entry.ok
                    else _failed_outcome(entry.error)
                )
                accepted = outcome.accepted
                row = TrialRow(
                    trial_id=next(ctx.trial_counter),
                    round=polish_round,
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
                if not accepted:
                    continue
                traj = trajectories[t_idx]
                if outcome.score > traj["best_score"]:
                    base = improved.get(t_idx)
                    if base is None or outcome.score > base[1]:
                        # Recover probe unit vector from p_idx.
                        # hooke_jeeves_propose returns [plus_0, minus_0, plus_1, minus_1, ...]
                        dim = p_idx // 2
                        sign = +1 if p_idx % 2 == 0 else -1
                        new_unit = list(traj["unit"])
                        new_unit[dim] = min(1.0, max(0.0, new_unit[dim] + sign * traj["step"][dim]))
                        improved[t_idx] = (new_unit, outcome.score, dict(entry.metrics), params)
            for t_idx, traj in enumerate(trajectories):
                if not traj["active"]:
                    continue
                if t_idx in improved:
                    new_unit, sc, m, p = improved[t_idx]
                    traj["unit"] = new_unit
                    traj["best_score"] = sc
                    traj["best_metrics"] = m
                    traj["best_params"] = p
                else:
                    traj["step"] = [s * 0.5 for s in traj["step"]]
                    if all(s < step_min for s in traj["step"]):
                        traj["active"] = False
            if ctx.persist_writer is not None:
                ctx.persist_writer.flush()
            polish_round += 1

        # Final winner = best across LHS + every polish trajectory.
        best = lhs_winner
        for traj in trajectories:
            if traj["best_score"] > best.train_score:
                best = FoldWinner(
                    fold_index=ctx.fold_index,
                    params=dict(traj["best_params"]),
                    train_metrics=dict(traj["best_metrics"]),
                    train_score=traj["best_score"],
                )
        if best.train_score == float("-inf"):
            return _fallback_winner(ctx.trial_rows, ctx.fold_index)
        # ``n_dims`` informs the predictor's per-trajectory budget; unused here
        # to satisfy ruff's unused-variable check.
        del n_dims
        return best

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        knobs = optim.lhs_polish if optim.lhs_polish is not None else LhsPolishKnobs()
        n_dims = max(1, len(optim.space))
        polish_cost = knobs.top_k_polish * 2 * n_dims * knobs.max_polish_iters
        per_fold = knobs.lhs_n + polish_cost
        return per_fold * folds_count + folds_count * folds_count


__all__ = ["LhsPolishSearch"]
