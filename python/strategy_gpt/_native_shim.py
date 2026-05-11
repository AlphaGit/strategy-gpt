"""Lazy import wrapper for the compiled native module.

The native extension is built by ``maturin develop`` (see
``crates/py-bindings/``); pure-Python tooling (ruff, mypy, ``pytest`` runs
that don't exercise native code) should remain usable even when the
extension has not been built yet.

Callers use :func:`require_native` to access the module — it raises a clear
``RuntimeError`` with the build hint instead of an ``ImportError`` that
points nowhere obvious.

Module shape (mirrors `crates/py-bindings/src/lib.rs`):
- ``strategy_gpt._native.gateway.DataGateway``
- ``strategy_gpt._native.ledger.Ledger``
- ``strategy_gpt._native.objectives.{validate_spec, evaluate_spec, engine_metrics}``
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Final

_BUILD_HINT: Final = (
    "the `strategy_gpt._native` extension has not been built. "
    "Run `maturin develop -m crates/py-bindings/Cargo.toml` from the repo root."
)


def require_native() -> ModuleType:
    """Return the compiled `strategy_gpt._native` module or raise."""
    try:
        return importlib.import_module("strategy_gpt._native")
    except ImportError as e:  # pragma: no cover — exercised when uncompiled
        raise RuntimeError(_BUILD_HINT) from e


def native_available() -> bool:
    """Cheap check used by tests to skip on uncompiled environments."""
    try:
        importlib.import_module("strategy_gpt._native")
    except ImportError:
        return False
    else:
        return True
