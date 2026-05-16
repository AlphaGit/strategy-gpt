"""Shared protocol + per-fold search context for the strategy pattern.

Each search method lives in its own module under :mod:`strategy_gpt.search`
and implements the :class:`SearchMethod` protocol. The optimization
runner and benchmark predictor dispatch through the registry in
:mod:`strategy_gpt.search.__init__` — neither knows the per-method
internals.

Two shapes of search methods exist:

- **Per-fold** (the common case): the method evaluates each fold
  independently. It implements :meth:`SearchMethod.search_fold` and the
  orchestrator's per-fold loop calls it once per fold.
- **Global** (e.g., successive halving): the method evaluates
  candidates across many folds simultaneously, so the per-fold loop
  doesn't fit. The method sets ``owns_global_loop = True`` and
  implements :meth:`SearchMethod.search_global` returning the
  fold-winner list directly.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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


@dataclass(frozen=True)
class GlobalSearchContext:
    """Cross-fold context used by methods that own the per-fold loop.

    Mirrors :class:`FoldSearchContext` but with the full ``folds`` list
    in place of a single ``(fold_index, fold)`` pair. The method is
    responsible for producing a :class:`FoldWinner` per candidate it
    wants cross-validated; the orchestrator's cross-OOS phase consumes
    that list unchanged.
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
    folds: Sequence[FoldRange]
    parallelism: int
    trial_counter: Iterator[int]
    trial_rows: list[TrialRow]
    poll_interval_secs: float
    persist_writer: _PersistWriter | None


class SearchMethod(Protocol):
    """Per-fold search method.

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


@runtime_checkable
class GlobalSearchMethod(Protocol):
    """Search method that owns the cross-fold loop (e.g., successive halving).

    The orchestrator's per-fold loop is skipped when the registered
    method satisfies this protocol; :meth:`search_global` runs once and
    returns the fold-winner list directly. Methods implementing this
    protocol still implement :class:`SearchMethod` (``name`` +
    ``planned_run_count``) so the registry stays uniform.
    """

    name: str

    def search_global(self, ctx: GlobalSearchContext) -> list[FoldWinner]:
        """Drive the cross-fold search; returns one FoldWinner per candidate."""

    def planned_run_count(self, optim: OptimizeBlock, folds_count: int) -> int: ...


__all__ = [
    "FoldSearchContext",
    "GlobalSearchContext",
    "GlobalSearchMethod",
    "SearchMethod",
]
