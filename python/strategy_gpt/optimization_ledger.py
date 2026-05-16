"""Persistence layout for optimization runs.

Directory layout (see ``design.md §6`` of the ``optimize-command`` change)::

    ledger/
      optimizations/
        <opt_id>/
          manifest.json
          trials.parquet
          best.json
          benchmark.json (only if --benchmark ran)
      optimizations.sqlite        # cross-run index

Streaming model: trial rows accumulate in memory per round and are
flushed to ``trials.parquet`` in append-friendly chunks (one chunk per
``flush``); :meth:`OptimizationLedger.finish` writes ``best.json`` and
marks the SQLite index row complete.

Replay-by-trial reconstructs a single-run :class:`BatchSpec` from a
parquet trial row plus the run manifest and re-submits to the engine,
producing a byte-identical :class:`BacktestResult` (no payload was
persisted — replay reproduces it on demand).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .benchmark import BenchmarkReport
from .folds import FoldRange
from .optimization_runner import (
    CrossValidationOutcome,
    OptimizationResult,
    TrialRow,
)

_OPTIMIZATIONS_SUBDIR = "optimizations"
_SQLITE_FILENAME = "optimizations.sqlite"

_TRIAL_SCHEMA = pa.schema(
    [
        ("trial_id", pa.uint64()),
        ("round", pa.uint32()),
        ("phase", pa.string()),
        ("fold_index", pa.uint32()),
        ("params", pa.string()),
        ("seed", pa.int64()),
        ("metrics", pa.string()),
        ("score", pa.float64()),
        ("accepted", pa.bool_()),
        ("reject_reason", pa.string()),
        ("wall_secs", pa.float64()),
    ]
)


def _ensure_index(sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(sqlite_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS optimizations (
                opt_id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                trial_count INTEGER NOT NULL DEFAULT 0,
                parent_strategy_artifact TEXT,
                manifest_path TEXT NOT NULL
            )
            """
        )
        con.commit()
    finally:
        con.close()


class OptimizationLedger:
    """Writer that satisfies the :class:`_PersistWriter` protocol.

    Holds in-memory buffers for trial rows and flushes them per round.
    """

    def __init__(self, ledger_root: Path | str) -> None:
        self.root = Path(ledger_root)
        self.opt_dir: Path | None = None
        self.manifest_path: Path | None = None
        self.trials_path: Path | None = None
        self._writer: pq.ParquetWriter | None = None
        self._buffer: list[TrialRow] = []
        self._trial_count: int = 0
        self._chunk_size: int = 1024
        self._opt_id: str | None = None
        self._sqlite_path: Path = self.root / _SQLITE_FILENAME
        self._started_at: datetime | None = None
        self._name: str | None = None
        self._parent_artifact: str | None = None

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @chunk_size.setter
    def chunk_size(self, value: int) -> None:
        if value < 1:
            msg = f"chunk_size must be >= 1, got {value}."
            raise ValueError(msg)
        self._chunk_size = value

    def start(  # noqa: PLR0913 — manifest fields are part of the wire shape.
        self,
        *,
        experiment: Any,  # noqa: ANN401 — late-bound to avoid import cycle.
        objective: Mapping[str, Any],
        dataset_manifest: str,
        artifact_path: Path,
        opt_id: str,
        resolved_parallelism: int,
        seed: int,
        started_at: datetime,
        folds: Sequence[FoldRange],
    ) -> None:
        self._opt_id = opt_id
        self._started_at = started_at
        self._name = experiment.optimize.persist.name if experiment.optimize is not None else opt_id
        self._parent_artifact = str(artifact_path)
        self.opt_dir = self.root / _OPTIMIZATIONS_SUBDIR / opt_id
        self.opt_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.opt_dir / "manifest.json"
        self.trials_path = self.opt_dir / "trials.parquet"
        manifest = {
            "opt_id": opt_id,
            "name": self._name,
            "artifact_path": str(artifact_path),
            "dataset_manifest": dataset_manifest,
            "resolved_parallelism": resolved_parallelism,
            "seed": seed,
            "method": experiment.optimize.method if experiment.optimize is not None else None,
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "status": "running",
            "folds": [
                {
                    "index": i,
                    "train": {
                        "start": f.train.start.isoformat(),
                        "end": f.train.end.isoformat(),
                    },
                    "oos": {
                        "start": f.oos.start.isoformat(),
                        "end": f.oos.end.isoformat(),
                    },
                    "warmup_bars": f.warmup_bars,
                }
                for i, f in enumerate(folds)
            ],
            "experiment_spec": _experiment_json(experiment),
            "objective": dict(objective),
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        _ensure_index(self._sqlite_path)
        con = sqlite3.connect(self._sqlite_path)
        try:
            con.execute(
                """
                INSERT INTO optimizations
                  (opt_id, name, status, started_at, finished_at, trial_count,
                   parent_strategy_artifact, manifest_path)
                VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
                ON CONFLICT(opt_id) DO UPDATE SET
                  status = excluded.status,
                  started_at = excluded.started_at,
                  finished_at = NULL,
                  trial_count = 0,
                  parent_strategy_artifact = excluded.parent_strategy_artifact,
                  manifest_path = excluded.manifest_path
                """,
                (
                    opt_id,
                    self._name,
                    "running",
                    started_at.isoformat(),
                    str(artifact_path),
                    str(self.manifest_path),
                ),
            )
            con.commit()
        finally:
            con.close()

    def emit_row(self, row: TrialRow) -> None:
        self._buffer.append(row)
        self._trial_count += 1
        if len(self._buffer) >= self._chunk_size:
            self._flush()

    def flush(self) -> None:
        """Write any buffered rows to disk. Called at round boundaries."""
        self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        if self.trials_path is None:
            msg = "OptimizationLedger: emit_row called before start()."
            raise RuntimeError(msg)
        if self._writer is None:
            self._writer = pq.ParquetWriter(self.trials_path, _TRIAL_SCHEMA, compression="zstd")
        rows = self._buffer
        self._buffer = []
        cols = {
            "trial_id": pa.array([r.trial_id for r in rows], type=pa.uint64()),
            "round": pa.array([r.round for r in rows], type=pa.uint32()),
            "phase": pa.array([r.phase for r in rows], type=pa.string()),
            "fold_index": pa.array([r.fold_index for r in rows], type=pa.uint32()),
            "params": pa.array(
                [json.dumps(r.params, sort_keys=True, default=_default) for r in rows],
                type=pa.string(),
            ),
            "seed": pa.array([r.seed for r in rows], type=pa.int64()),
            "metrics": pa.array(
                [json.dumps(r.metrics, sort_keys=True, default=_default) for r in rows],
                type=pa.string(),
            ),
            "score": pa.array([_safe_float(r.score) for r in rows], type=pa.float64()),
            "accepted": pa.array([r.accepted for r in rows], type=pa.bool_()),
            "reject_reason": pa.array([r.reject_reason for r in rows], type=pa.string()),
            "wall_secs": pa.array([r.wall_secs for r in rows], type=pa.float64()),
        }
        table = pa.Table.from_pydict(cols, schema=_TRIAL_SCHEMA)
        self._writer.write_table(table)

    def finish(self, result: OptimizationResult) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if self.opt_dir is None or self.manifest_path is None:
            msg = "OptimizationLedger: finish() called before start()."
            raise RuntimeError(msg)
        finished = result.finished_at.isoformat()
        best_path = self.opt_dir / "best.json"
        best_path.write_text(json.dumps(_best_payload(result), indent=2, sort_keys=True))
        # Update manifest with finished_at + status.
        manifest = json.loads(self.manifest_path.read_text())
        manifest["finished_at"] = finished
        manifest["status"] = "completed"
        manifest["trial_count"] = len(result.trial_rows)
        self.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        # Update SQLite index.
        con = sqlite3.connect(self._sqlite_path)
        try:
            con.execute(
                """
                UPDATE optimizations
                SET status = ?, finished_at = ?, trial_count = ?
                WHERE opt_id = ?
                """,
                ("completed", finished, len(result.trial_rows), self._opt_id),
            )
            con.commit()
        finally:
            con.close()

    def write_benchmark(self, report: BenchmarkReport) -> None:
        if self.opt_dir is None:
            msg = "OptimizationLedger: write_benchmark called before start()."
            raise RuntimeError(msg)
        path = self.opt_dir / "benchmark.json"
        path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True))


def _safe_float(x: float) -> float:
    # parquet float64 cannot serialize -inf reliably across readers; clamp.
    if x == float("-inf"):
        return -1e308
    if x == float("inf"):
        return 1e308
    return x


def _default(obj: Any) -> Any:  # noqa: ANN401 — heterogeneous JSON serializer.
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    msg = f"Type {type(obj).__name__} is not JSON-serializable"
    raise TypeError(msg)


def _experiment_json(experiment: Any) -> dict[str, Any]:  # noqa: ANN401
    payload: dict[str, Any] = json.loads(experiment.model_dump_json())
    return payload


def _best_payload(result: OptimizationResult) -> dict[str, Any]:
    final = result.final
    return {
        "opt_id": result.opt_id,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "fold_winners": [
            {
                "fold_index": fw.fold_index,
                "params": fw.params,
                "train_metrics": fw.train_metrics,
                "train_score": _safe_float(fw.train_score),
            }
            for fw in result.fold_winners
        ],
        "cross_validation": [_cv_payload(cv) for cv in result.cross_validation],
        "final": _cv_payload(final) if final is not None else None,
        "resolved_parallelism": result.resolved_parallelism,
        "seed": result.seed,
    }


def _cv_payload(cv: CrossValidationOutcome) -> dict[str, Any]:
    return {
        "fold_index": cv.fold_index,
        "params": cv.params,
        "oos_metrics": cv.oos_metrics,
        "aggregate_metrics": cv.aggregate_metrics,
        "aggregate_score": _safe_float(cv.aggregate_score),
        "aggregate_accepted": cv.aggregate_accepted,
        "aggregate_reject_reason": cv.aggregate_reject_reason,
        "score_variance": cv.score_variance,
    }


# ---------------------------------------------------------------------------
# Reader / inspect / replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialRecord:
    """Materialized trial row from ``trials.parquet`` for inspect/replay."""

    trial_id: int
    round: int
    phase: str
    fold_index: int
    params: dict[str, Any]
    seed: int
    metrics: dict[str, Any]
    score: float
    accepted: bool
    reject_reason: str
    wall_secs: float


def read_manifest(opt_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((opt_dir / "manifest.json").read_text())
    return payload


def read_best(opt_dir: Path) -> dict[str, Any] | None:
    path = opt_dir / "best.json"
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text())
    return payload


def read_trials(opt_dir: Path) -> list[TrialRecord]:
    path = opt_dir / "trials.parquet"
    if not path.exists():
        return []
    table = pq.read_table(path)
    rows = table.to_pylist()
    return [
        TrialRecord(
            trial_id=int(r["trial_id"]),
            round=int(r["round"]),
            phase=r["phase"],
            fold_index=int(r["fold_index"]),
            params=json.loads(r["params"]),
            seed=int(r["seed"]),
            metrics=json.loads(r["metrics"]),
            score=float(r["score"]),
            accepted=bool(r["accepted"]),
            reject_reason=r["reject_reason"],
            wall_secs=float(r["wall_secs"]),
        )
        for r in rows
    ]


def find_trial(opt_dir: Path, trial_id: int) -> TrialRecord | None:
    for r in read_trials(opt_dir):
        if r.trial_id == trial_id:
            return r
    return None


def build_replay_batch(manifest: Mapping[str, Any], trial: TrialRecord) -> dict[str, Any]:
    """Reconstruct a single-run :class:`BatchSpec` dict from a recorded trial.

    The manifest carries the full experiment-spec, the dataset manifest hash,
    and the resolved fold ranges. The trial row carries the candidate
    params, seed, and the phase that identifies which fold slice to replay.
    """
    folds = manifest["folds"]
    fold = folds[trial.fold_index]
    phase = trial.phase
    slice_ = fold["train"] if phase.startswith("train_fold_") else fold["oos"]
    es = manifest["experiment_spec"]
    template = es["runs"][0]
    merged_params = {**dict(template.get("params", {})), **trial.params}
    run = {
        "params": merged_params,
        "modes": list(template.get("modes", [{"kind": "plain"}])),
        "seed": trial.seed,
        "slice": dict(slice_),
    }
    eng = es.get("engine") or {}
    engine_cfg = {
        "fill_model": eng.get("fill_model", "NextBarOpen"),
        "initial_capital": eng.get("initial_capital", 100_000.0),
        "commission_per_fill": eng.get("commission_per_fill", 0.0),
        "slippage_bps": 0.0,
        "sanity": eng.get("sanity", {"max_intent_size": 1.0e9, "max_position_size": 1.0e9}),
    }
    strategy = es.get("strategy_label") or Path(es.get("artifact", "")).stem
    return {
        "strategy": strategy,
        "dataset": manifest["dataset_manifest"],
        "runs": [run],
        "engine": engine_cfg,
        "parallelism": 1,
        "failure_mode": "abort",
    }


def opt_dir_for(ledger_root: Path | str, opt_id: str) -> Path:
    return Path(ledger_root) / _OPTIMIZATIONS_SUBDIR / opt_id


def index_path(ledger_root: Path | str) -> Path:
    return Path(ledger_root) / _SQLITE_FILENAME


__all__ = [
    "OptimizationLedger",
    "TrialRecord",
    "build_replay_batch",
    "find_trial",
    "index_path",
    "opt_dir_for",
    "read_best",
    "read_manifest",
    "read_trials",
]
