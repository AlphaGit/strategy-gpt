"""``method: random`` — uniform random sampling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..experiment_spec import OptimizeBlock
from ..optimizer import RandomParam, RandomSearcher
from .base import FoldSearchContext
from .one_shot import search_one_shot

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..optimization_runner import FoldWinner
    from ..optimizer import ParamSet


def _candidates(optim: OptimizeBlock, space: dict[str, RandomParam]) -> Iterable[ParamSet]:
    knobs = optim.random
    assert knobs is not None, "random method requires the `random` knob block"  # noqa: S101
    return RandomSearcher(space=space, n_iter=knobs.n_samples, seed=optim.seed).candidates()


class RandomSearch:
    """Single packed batch per fold of ``n_samples`` random candidates."""

    name = "random"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        return search_one_shot(ctx, _candidates(ctx.optim, ctx.space))

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        n = optim.random.n_samples if optim.random is not None else 0
        return n * folds_count + folds_count * folds_count


__all__ = ["RandomSearch"]
