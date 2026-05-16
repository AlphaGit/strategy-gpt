"""``method: sobol`` — Owen-scrambled quasi-random search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..experiment_spec import OptimizeBlock, SobolKnobs
from ..optimizer import RandomParam, SobolSearcher, _next_power_of_two
from .base import FoldSearchContext
from .one_shot import search_one_shot

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..optimizer import ParamSet
    from ..optimization_runner import FoldWinner


def _candidates(optim: OptimizeBlock, space: dict[str, RandomParam]) -> Iterable[ParamSet]:
    knobs = optim.sobol if optim.sobol is not None else SobolKnobs()
    return SobolSearcher(
        space=space,
        n_points=knobs.n_points,
        scramble=knobs.scramble,
        owen_seed=knobs.owen_seed if knobs.scramble else optim.seed,
    ).candidates()


class SobolSearch:
    """Single packed batch per fold — every Sobol point evaluated at once."""

    name = "sobol"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        return search_one_shot(ctx, _candidates(ctx.optim, ctx.space))

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        knobs = optim.sobol if optim.sobol is not None else SobolKnobs()
        n = _next_power_of_two(knobs.n_points)
        return n * folds_count + folds_count * folds_count


__all__ = ["SobolSearch"]
