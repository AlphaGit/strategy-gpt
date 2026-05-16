"""Shared protocol + per-fold search context for the strategy pattern.

Each search method lives in its own module under :mod:`strategy_gpt.search`
and implements the :class:`SearchMethod` protocol. The optimization
runner and benchmark predictor dispatch through the registry in
:mod:`strategy_gpt.search.__init__` — neither knows the per-method
internals.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..engine import Engine
    from ..experiment_spec import ExperimentSpec, OptimizeBlock, RunConfig
    from ..folds import FoldRange
    from ..optimization_runner import FoldWinner, TrialRow, _PersistWriter
    from ..optimizer import RandomParam
    from ..types import Bar


@dataclass(frozen=True)
class FoldSearchContext:
    """Everything a search method needs to drive one fold's train search.

    The orchestrator builds one of these per (fold, method) call and
    hands it to the registered :class:`SearchMethod`. The context carries
    the engine handle, the dataset, the fold's slice, the trial counter
    (so trial_ids stay monotonic across the whole run), and the persist
    writer so each method can stream rows out as it goes.
    """

    experiment: ExperimentSpec
    objective: Mapping[str, Any]
    engine: Engine
    artifact_path: Path
    bars: list[Bar]
    dataset_manifest: str
    template: RunConfig
    optim: OptimizeBlock
    space: dict[str, RandomParam]
    fold_index: int
    fold: FoldRange
    parallelism: int
    trial_counter: Iterator[int]
    trial_rows: list[TrialRow]
    poll_interval_secs: float
    persist_writer: _PersistWriter | None


class SearchMethod(Protocol):
    """One search method's strategy-pattern surface.

    ``name`` is the method enum value (matches ``optimize.method`` in the
    experiment-spec). ``search_fold`` drives one fold's train-side
    search and returns its winner; ``planned_run_count`` predicts the
    total backtest count the method will commission so the benchmark
    predictor can extrapolate wall time without method-specific
    branches.
    """

    name: str

    def search_fold(self, ctx: FoldSearchContext) -> FoldWinner:
        """Drive one fold's train search to completion."""

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int:
        """Total backtest count the method commissions for one optimization run.

        Always includes the ``folds_count ** 2`` cross-OOS phase the
        orchestrator runs after every method completes.
        """


__all__ = ["FoldSearchContext", "SearchMethod"]
