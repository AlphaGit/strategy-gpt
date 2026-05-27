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


def _spec_with_optimize(tmp_path: Path, **overrides: Any) -> Path:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "deadbeef"},
        "runs": [
            {
                "params": {"size": 100.0},
                "modes": [{"kind": "plain"}],
                "seed": 1,
                "slice": {
                    "start": "2018-01-01T00:00:00Z",
                    "end": "2026-01-01T00:00:00Z",
                },
            }
        ],
        "folds": {"count": 4, "scheme": "rolling", "gap": 0},
        "optimize": {
            "method": "grid",
            "seed": 42,
            "space": {
                "vol_lo": {"type": "float", "low": 0.2, "high": 0.5, "step": 0.05},
                "vol_hi": {"type": "float", "low": 0.6, "high": 1.0, "step": 0.1},
            },
            "grid": {"resolution": 5},
            "persist": {"root": "./out", "name": "run1"},
        },
    }
    spec.update(overrides)
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    return path


def test_optimize_requires_folds(tmp_path: Path) -> None:
    path = _spec_with_optimize(tmp_path, folds=None)
    # Strip folds entirely by reloading and rewriting.
    raw = yaml.safe_load(path.read_text())
    del raw["folds"]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(Exception, match="folds"):
        espec.load(path)


def test_search_space_disjoint_from_fixed_params(tmp_path: Path) -> None:
    path = _spec_with_optimize(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["runs"][0]["params"]["vol_lo"] = 0.3  # collides with optimize.space
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(Exception, match=r"vol_lo"):
        espec.load(path)


def test_validate_search_space_rejects_unknown_keys(tmp_path: Path) -> None:
    path = _spec_with_optimize(tmp_path)
    spec = espec.load(path)
    with pytest.raises(ValueError, match="vol_hi"):
        espec.validate_search_space(spec, declared_params={"vol_lo", "size"})


def test_validate_search_space_accepts_when_all_declared(tmp_path: Path) -> None:
    path = _spec_with_optimize(tmp_path)
    spec = espec.load(path)
    espec.validate_search_space(spec, declared_params={"vol_lo", "vol_hi", "size"})


def test_folds_count_floor(tmp_path: Path) -> None:
    path = _spec_with_optimize(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["folds"]["count"] = 1
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(Exception, match="count"):
        espec.load(path)


def test_top_level_slice_and_seed_inherit_into_runs(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "deadbeef"},
        "seed": 99,
        "slice": {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-12-31T00:00:00Z",
        },
        "runs": [
            {"params": {"x": 1.0}},
            {"params": {"x": 2.0}},
            {"params": {"x": 3.0}},
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    parsed = espec.load(path)
    assert len(parsed.runs) == 3
    for run in parsed.runs:
        assert run.seed == 99
        assert run.slice.start.year == 2024
        assert run.slice.end.year == 2024


def test_per_run_slice_overrides_top_level(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "deadbeef"},
        "slice": {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-12-31T00:00:00Z",
        },
        "seed": 1,
        "runs": [
            {"params": {"x": 1.0}},  # inherits both
            {
                "params": {"x": 2.0},
                "seed": 7,
                "slice": {
                    "start": "2022-01-01T00:00:00Z",
                    "end": "2022-12-31T00:00:00Z",
                },
            },
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    parsed = espec.load(path)
    assert parsed.runs[0].seed == 1
    assert parsed.runs[0].slice.start.year == 2024
    assert parsed.runs[1].seed == 7
    assert parsed.runs[1].slice.start.year == 2022


def test_missing_slice_everywhere_rejected(tmp_path: Path) -> None:
    spec = {
        "artifact": "./libfoo.dylib",
        "bars": {"dataset": "deadbeef"},
        "runs": [
            {"params": {"x": 1.0}},
        ],
    }
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(spec))
    (tmp_path / "libfoo.dylib").write_bytes(b"")
    with pytest.raises(Exception, match="slice"):
        espec.load(path)
