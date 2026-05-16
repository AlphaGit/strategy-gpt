"""``method: bayesian`` — Tree-structured Parzen Estimator (placeholder).

The TPE searcher exists in :mod:`strategy_gpt.optimizer` but the
per-fold orchestrator hook is not wired yet. The runner surfaces this
explicitly so callers don't silently fall back to another method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..experiment_spec import OptimizeBlock
from .base import FoldSearchContext

if TYPE_CHECKING:
    from ..optimization_runner import FoldWinner


class BayesianSearch:
    """TPE per-fold dispatch — not implemented yet."""

    name = "bayesian"

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        del ctx
        msg = (
            "method='bayesian' (TPE) is not yet wired into the per-fold "
            "orchestrator. Use `recursive_grid`, `grid`, `random`, `sobol`, "
            "`differential_evolution`, or `cma_es` for now."
        )
        raise NotImplementedError(msg)

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        n = optim.bayesian.n_iter if optim.bayesian is not None else 0
        return n * folds_count + folds_count * folds_count


__all__ = ["BayesianSearch"]
