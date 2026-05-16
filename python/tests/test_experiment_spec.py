"""Unit tests for `strategy_gpt.experiment_spec`.

Cover round-trip parsing, both `bars` variants, `parallelism: auto`
resolution under each OS path, and the legacy `batch.json` rejection
contract.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml  # type: ignore[import-untyped]

from strategy_gpt import experiment_spec as espec


def _base_yaml(tmp_path: Path) -> Path:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "deadbeef"},
        "engine": {
            "fill_model": "NextBarOpen",
            "initial_capital": 100000.0,
            "commission_per_fill": 0.0,
            "sanity": {"max_intent_size": 1e9, "max_position_size": 1e9},
        },
        "runs": [
            {
                "params": {"vol_lo": 0.3},
                "modes": [{"kind": "plain"}],
                "seed": 7,
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                },
            }
        ],
        "parallelism": 2,
    }
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    return path


def test_round_trip_dataset_variant(tmp_path: Path) -> None:
    path = _base_yaml(tmp_path)
    parsed = espec.load(path)
    assert isinstance(parsed.bars, espec.DatasetRef)
    assert parsed.bars.dataset == "deadbeef"
    assert parsed.artifact.is_absolute()
    assert parsed.artifact.name == "libfoo.dylib"
    assert parsed.runs[0].seed == 7
    assert parsed.resolved_parallelism() == 2


def test_request_variant(tmp_path: Path) -> None:
    spec: dict[str, Any] = {
        "artifact": "./libfoo.dylib",
        "bars": {
            "request": {
                "provider": "yfinance",
                "symbol": "VXX",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-12-31T00:00:00Z",
                "resolution": "Day",
                "adjustment": "back_adjusted",
            }
        },
        "runs": [
            {
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                }
            }
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    parsed = espec.load(path)
    assert isinstance(parsed.bars, espec.RequestRef)
    assert parsed.bars.request.symbol == "VXX"


def test_bars_xor_both_rejected(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {
            "dataset": "abc",
            "request": {
                "provider": "x",
                "symbol": "Y",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-12-31T00:00:00Z",
                "resolution": "Day",
                "adjustment": "raw",
            },
        },
        "runs": [
            {
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                }
            }
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    with pytest.raises(Exception, match="exactly one of"):
        espec.load(path)


def test_bars_xor_neither_rejected(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {},
        "runs": [
            {
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                }
            }
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    with pytest.raises(Exception, match="must declare one of"):
        espec.load(path)


def test_slippage_bps_rejected(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "abc"},
        "engine": {"slippage_bps": 1.5},
        "runs": [
            {
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                }
            }
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    with pytest.raises(Exception, match="slippage_bps"):
        espec.load(path)


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity")),
    reason="linux affinity path",
)
def test_auto_parallelism_linux_affinity(tmp_path: Path) -> None:
    path = _base_yaml(tmp_path)
    payload = yaml.safe_load(path.read_text())
    payload["parallelism"] = "auto"
    path.write_text(yaml.safe_dump(payload))
    parsed = espec.load(path)
    with mock.patch.object(os, "sched_getaffinity", return_value={0, 1, 2, 3}):
        assert parsed.resolved_parallelism() == 3


def test_auto_parallelism_non_linux(tmp_path: Path) -> None:
    path = _base_yaml(tmp_path)
    payload = yaml.safe_load(path.read_text())
    payload["parallelism"] = "auto"
    path.write_text(yaml.safe_dump(payload))
    parsed = espec.load(path)
    with (
        mock.patch.object(sys, "platform", "darwin"),
        mock.patch.object(os, "cpu_count", return_value=8),
    ):
        assert parsed.resolved_parallelism() == 7


def test_auto_parallelism_clamps_to_one(tmp_path: Path) -> None:
    path = _base_yaml(tmp_path)
    payload = yaml.safe_load(path.read_text())
    payload["parallelism"] = "auto"
    path.write_text(yaml.safe_dump(payload))
    parsed = espec.load(path)
    with (
        mock.patch.object(sys, "platform", "darwin"),
        mock.patch.object(os, "cpu_count", return_value=1),
    ):
        assert parsed.resolved_parallelism() == 1


def test_legacy_batch_json_rejected(tmp_path: Path) -> None:
    legacy = {
        "strategy": "vxx-local",
        "dataset": "vxx-local-demo",
        "runs": [
            {
                "params": {},
                "modes": [{"kind": "plain"}],
                "seed": 1,
                "slice": {
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-12-31T00:00:00Z",
                },
            }
        ],
        "engine": {
            "fill_model": "NextBarOpen",
            "initial_capital": 100000.0,
            "commission_per_fill": 0.0,
            "slippage_bps": 0.0,
            "sanity": {"max_intent_size": 1e9, "max_position_size": 1e9},
        },
        "parallelism": 1,
    }
    path = tmp_path / "legacy_batch.json"
    path.write_text(json.dumps(legacy))
    with pytest.raises(Exception, match=r"legacy `batch\.json`"):
        espec.load(path)


def test_to_batch_spec_injects_slippage_zero(tmp_path: Path) -> None:
    path = _base_yaml(tmp_path)
    parsed = espec.load(path)
    batch = parsed.to_batch_spec("manifest-hash")
    assert batch["dataset"] == "manifest-hash"
    assert batch["strategy"] == "vxx-local" or batch["strategy"] == "libfoo"
    assert batch["engine"]["slippage_bps"] == 0.0
    assert batch["engine"]["fill_model"] == "NextBarOpen"
    assert batch["parallelism"] == 2
    assert batch["runs"][0]["seed"] == 7
