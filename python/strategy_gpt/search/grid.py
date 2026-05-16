"""``method: grid`` — exhaustive cartesian-product search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..experiment_spec import (
    ChoiceParam as SpecChoiceParam,
)
from ..experiment_spec import (
    FloatParam as SpecFloatParam,
)
from ..experiment_spec import (
    IntParam as SpecIntParam,
)
from ..experiment_spec import OptimizeBlock
from ..optimizer import GridSearcher, RandomParam
from .base import FoldSearchContext
from .one_shot import search_one_shot

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..optimization_runner import FoldWinner
    from ..optimizer import ParamSet


_DEFAULT_RESOLUTION = 10


def _grid_resolution(optim: OptimizeBlock) -> int:
    grid = optim.grid
    if grid is not None and grid.resolution is not None:
        return int(grid.resolution)
    return _DEFAULT_RESOLUTION


def _grid_values(optim: OptimizeBlock) -> dict[str, list[object]]:
    from ..optimization_runner import _grid_values_for

    res = _grid_resolution(optim)
    return {k: _grid_values_for(k, p, res) for k, p in optim.space.items()}


def _candidates(optim: OptimizeBlock, space: dict[str, RandomParam]) -> Iterable[ParamSet]:
    del space  # grid reads optim.space directly to keep step/choice info.
    return GridSearcher(grid=_grid_values(optim)).candidates()


class GridSearch:
    """Evaluate every grid point in one packed batch."""

    name = "grid"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        return search_one_shot(ctx, _candidates(ctx.optim, ctx.space))

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        default_res = _grid_resolution(optim)
        size = 1
        for param in optim.space.values():
            if isinstance(param, SpecChoiceParam):
                size *= len(param.choices)
            elif isinstance(param, SpecIntParam):
                if param.step is not None:
                    points = ((param.high - param.low) // param.step) + 1
                    size *= int(points)
                else:
                    size *= default_res
            elif isinstance(param, SpecFloatParam):
                if param.step is not None:
                    points = int((param.high - param.low) / param.step) + 1
                    size *= max(points, 1)
                else:
                    size *= default_res
        return size * folds_count + folds_count * folds_count


__all__ = ["GridSearch"]
