"""Search-method strategy registry.

The orchestrator (:mod:`strategy_gpt.optimization_runner`) and the
benchmark predictor (:mod:`strategy_gpt.benchmark`) both dispatch
through this registry — neither file imports any per-method
implementation directly. Adding a new method is a two-step process:
write the method module, then register it here.
"""

from __future__ import annotations

from .base import FoldSearchContext, SearchMethod
from .bayesian import BayesianSearch
from .cma_es import CmaEsSearch
from .differential_evolution import DifferentialEvolutionSearch
from .grid import GridSearch
from .random_search import RandomSearch
from .recursive_grid import RecursiveGridSearch
from .sobol import SobolSearch

_METHODS: tuple[SearchMethod, ...] = (
    RecursiveGridSearch(),
    GridSearch(),
    RandomSearch(),
    BayesianSearch(),
    SobolSearch(),
    DifferentialEvolutionSearch(),
    CmaEsSearch(),
)

REGISTRY: dict[str, SearchMethod] = {m.name: m for m in _METHODS}


def get(method_name: str) -> SearchMethod:
    """Look up the strategy for a given method name; raises on unknown."""
    try:
        return REGISTRY[method_name]
    except KeyError as e:
        msg = f"unknown search method {method_name!r}; registered: {sorted(REGISTRY)}"
        raise ValueError(msg) from e


__all__ = ["REGISTRY", "FoldSearchContext", "SearchMethod", "get"]
