"""Tests for the lazy native-module shim.

`require_native()` raises a clear `RuntimeError` when the extension isn't
built. `native_available()` reports without raising. Once the extension
is built (via `maturin develop`), `native_available()` returns True and
the integration tests in `test_native_integration.py` (future) exercise
the real bindings.
"""

from __future__ import annotations

import pytest

from strategy_gpt._native_shim import native_available, require_native


def test_native_available_returns_bool() -> None:
    assert isinstance(native_available(), bool)


def test_require_native_returns_module_with_submodules() -> None:
    if not native_available():
        pytest.skip("native extension not built")
    module = require_native()
    for submod in ("gateway", "ledger", "objectives"):
        assert hasattr(module, submod), f"native module missing submodule `{submod}`"


def test_require_native_raises_when_missing() -> None:
    if native_available():
        pytest.skip("native extension is built; cannot test missing-path")
    with pytest.raises(RuntimeError, match="maturin develop"):
        require_native()
