"""Tests for parallelism auto-resolution (optimize-command tasks 5.x)."""

from __future__ import annotations

import os
import sys

import pytest

from strategy_gpt.parallelism import resolve_parallelism


def test_resolve_passes_through_positive_int() -> None:
    assert resolve_parallelism(5) == 5


def test_resolve_rejects_non_positive_int() -> None:
    with pytest.raises(ValueError, match="parallelism must be >= 1"):
        resolve_parallelism(0)


def test_resolve_rejects_unknown_string() -> None:
    with pytest.raises(ValueError, match="'auto'"):
        resolve_parallelism("xyz")  # type: ignore[arg-type]


def test_resolve_auto_subtracts_one_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        os,
        "sched_getaffinity",
        lambda _pid: {0, 1, 2, 3},
        raising=False,
    )
    assert resolve_parallelism("auto") == 3


def test_resolve_auto_floor_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0}, raising=False)
    assert resolve_parallelism("auto") == 1


def test_resolve_auto_falls_back_to_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert resolve_parallelism("auto") == 7
