"""Single source of truth for ``parallelism`` resolution.

The experiment-spec and CLI accept ``parallelism: auto``. Both the ``run``
and ``optimize`` entry points need an identical resolution rule so the
behaviour the user sees in either pipeline is the same and the optimizer
manifest records the value the engine actually saw.
"""

from __future__ import annotations

import os
import sys
from typing import Literal


def resolve_parallelism(value: int | Literal["auto"]) -> int:
    """Resolve a parallelism value to a concrete positive integer.

    ``auto`` resolves to ``max(1, usable_cpu_count - 1)``. Linux honours
    ``sched_getaffinity`` so cgroup-restricted hosts report the budget
    they were granted; other platforms fall back to ``os.cpu_count``.
    """
    if isinstance(value, int):
        if value < 1:
            msg = f"parallelism must be >= 1, got {value}."
            raise ValueError(msg)
        return value
    if value != "auto":
        msg = f"parallelism must be a positive int or the literal 'auto', got {value!r}."
        raise ValueError(msg)
    if sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity"):
        usable = len(os.sched_getaffinity(0))
    else:
        usable = os.cpu_count() or 1
    return max(1, usable - 1)


__all__ = ["resolve_parallelism"]
