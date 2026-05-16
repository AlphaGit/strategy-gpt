"""Persistence layout for optimization runs.

Also hosts the post-hoc reselection helper :func:`reselect` invoked by
``strategy-gpt optimize reselect``.

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
from datetime import UTC, datetime
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
from .selection import (
    SELECTION_METHODOLOGY,
    SelectionCandidate,
    SelectionDecision,
    SelectionKnobs,
    SelectionStatus,
    TrialPoint,
    run_selection,
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
            "selection_methodology": dict(SELECTION_METHODOLOGY),
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
            self._writer = pq.ParquetWriter(  # type: ignore[no-untyped-call]
                self.trials_path, _TRIAL_SCHEMA, compression="zstd"
            )
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
        self._writer.write_table(table)  # type: ignore[no-untyped-call]

    def finish(self, result: OptimizationResult) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()  # type: ignore[no-untyped-call]
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
    payload: dict[str, Any] = {
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
    payload.update(_selection_payload(result))
    return payload


def _selection_payload(result: OptimizationResult) -> dict[str, Any]:
    selection = result.selection
    if selection is None:
        return {
            "decision": None,
            "pbo": None,
            "deflated_sharpe": [],
            "sensitivity_score": [],
            "would_have_picked": None,
            "selection_methodology": dict(SELECTION_METHODOLOGY),
        }
    return {
        "decision": _decision_payload(selection),
        "pbo": _pbo_payload(selection),
        "deflated_sharpe": _dsr_payload(selection),
        "sensitivity_score": _sensitivity_payload(selection),
        "would_have_picked": selection.would_have_picked_trial_id,
        "selection_methodology": dict(selection.methodology) or dict(SELECTION_METHODOLOGY),
    }


def _decision_payload(decision: SelectionDecision) -> dict[str, Any]:
    return {
        "status": decision.status.value,
        "best_trial_id": decision.best_trial_id,
        "would_have_picked": decision.would_have_picked_trial_id,
        "reason": decision.reason,
        "ranking": list(decision.ranking),
        "robust_objective": decision.robust_objective,
        "force_override": decision.force_override,
        "pbo_threshold": decision.pbo_threshold,
        "effective_n": decision.effective_n,
        "history_size": decision.history_size,
    }


def _pbo_payload(decision: SelectionDecision) -> dict[str, Any]:
    return {
        "value": decision.pbo.pbo,
        "n_splits": decision.pbo.n_splits,
        "enumerated": decision.pbo.enumerated,
        "seed": decision.pbo.seed,
        "n_trials": decision.pbo.n_trials,
        "n_folds": decision.pbo.n_folds,
        "threshold": decision.pbo_threshold,
        "rejected": decision.status == SelectionStatus.REJECTED_PBO,
    }


def _dsr_payload(decision: SelectionDecision) -> list[dict[str, Any]]:
    return [
        {
            "trial_id": cs.trial_id,
            "raw_sharpe": _safe_float(cs.raw_sharpe),
            "expected_max_sharpe": _safe_float(cs.dsr.expected_max_sharpe),
            "sharpe_variance": _safe_float(cs.dsr.sharpe_variance),
            "z": _safe_float(cs.dsr.z),
            "dsr": _safe_float(cs.dsr.dsr),
        }
        for cs in decision.candidate_scores
    ]


def _sensitivity_payload(decision: SelectionDecision) -> list[dict[str, Any]]:
    return [
        {
            "trial_id": cs.trial_id,
            "raw_score": _safe_float(cs.sensitivity.raw_score),
            "neighborhood_mean": _safe_float(cs.sensitivity.neighborhood_mean),
            "neighborhood_std": _safe_float(cs.sensitivity.neighborhood_std),
            "robust_score": _safe_float(cs.sensitivity.robust_score),
            "neighbors_used": cs.sensitivity.neighbors_used,
        }
        for cs in decision.candidate_scores
    ]


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
    table = pq.read_table(path)  # type: ignore[no-untyped-call]
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


def reselect(  # noqa: PLR0913 — surface mirrors the CLI flags.
    opt_dir: Path,
    *,
    robust_objective: bool | None = None,
    pbo_threshold: float | None = None,
    force: bool = False,
    top_k: int | None = None,
    timestamp: str | None = None,
) -> Path:
    """Re-run the selection layer over an existing optimization's artifacts.

    Reads ``manifest.json`` (for the original experiment-spec, objective,
    and selection knobs), ``best.json`` (for the cross-validation
    candidates), and ``trials.parquet`` (for the trial history fed to the
    sensitivity layer). Writes a new ``best_<timestamp>.json`` next to
    the original ``best.json`` and updates the manifest's
    ``reselection_history`` with one entry per call. Returns the path of
    the new artifact.
    """
    manifest = read_manifest(opt_dir)
    best = read_best(opt_dir)
    if best is None:
        msg = f"reselect: no best.json found at {opt_dir}"
        raise FileNotFoundError(msg)
    objective: Mapping[str, Any] = manifest.get("objective", {})
    primary_name = "sharpe"
    primary = objective.get("primary") if isinstance(objective, Mapping) else None
    if isinstance(primary, Mapping):
        m = primary.get("metric")
        if isinstance(m, str):
            primary_name = m
    es = manifest.get("experiment_spec", {})
    optimize_block = es.get("optimize", {})
    knobs_raw = optimize_block.get("selection")
    knobs = (
        SelectionKnobs.model_validate(knobs_raw)
        if isinstance(knobs_raw, Mapping) and knobs_raw is not None
        else SelectionKnobs()
    )
    spec_robust = bool(optimize_block.get("robust_objective", False))
    robust = spec_robust if robust_objective is None else robust_objective

    cv_payloads = best.get("cross_validation", [])
    candidates: list[SelectionCandidate] = []
    for i, cv in enumerate(cv_payloads):
        oos = cv.get("oos_metrics", []) or []
        per_fold = [float(m.get(primary_name, 0.0)) if isinstance(m, Mapping) else 0.0 for m in oos]
        trade_count = int(sum(int(m.get("n_trades", 0)) for m in oos if isinstance(m, Mapping)))
        agg = cv.get("aggregate_metrics", {}) or {}
        candidates.append(
            SelectionCandidate(
                trial_id=i,
                params=dict(cv.get("params", {})),
                aggregate_score=float(cv.get("aggregate_score", 0.0)),
                aggregate_metrics={
                    k: float(v) for k, v in agg.items() if isinstance(v, (int, float))
                },
                per_fold_oos_primary=per_fold,
                trade_count=trade_count,
                accepted=bool(cv.get("aggregate_accepted", True)),
            )
        )

    history: list[TrialPoint] = []
    for r in read_trials(opt_dir):
        if not r.phase.startswith("train_fold_"):
            continue
        history.append(TrialPoint(params=dict(r.params), score=float(r.score)))

    if top_k is not None:
        knobs = knobs.model_copy(update={"pbo": knobs.pbo.model_copy(update={"top_k": top_k})})

    decision = run_selection(
        candidates,
        history,
        knobs,
        robust_objective=robust,
        force=force,
        pbo_threshold_override=pbo_threshold,
    )

    ts = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = opt_dir / f"best_{ts}.json"
    payload = _reselect_payload(best, decision)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    _record_reselection(opt_dir / "manifest.json", out_path, decision)
    return out_path


def _reselect_payload(
    original_best: Mapping[str, Any], decision: SelectionDecision
) -> dict[str, Any]:
    payload = dict(original_best)
    payload["decision"] = {
        "status": decision.status.value,
        "best_trial_id": decision.best_trial_id,
        "would_have_picked": decision.would_have_picked_trial_id,
        "reason": decision.reason,
        "ranking": list(decision.ranking),
        "robust_objective": decision.robust_objective,
        "force_override": decision.force_override,
        "pbo_threshold": decision.pbo_threshold,
        "effective_n": decision.effective_n,
        "history_size": decision.history_size,
    }
    payload["pbo"] = {
        "value": decision.pbo.pbo,
        "n_splits": decision.pbo.n_splits,
        "enumerated": decision.pbo.enumerated,
        "seed": decision.pbo.seed,
        "n_trials": decision.pbo.n_trials,
        "n_folds": decision.pbo.n_folds,
        "threshold": decision.pbo_threshold,
        "rejected": decision.status == SelectionStatus.REJECTED_PBO,
    }
    payload["deflated_sharpe"] = [
        {
            "trial_id": cs.trial_id,
            "raw_sharpe": _safe_float(cs.raw_sharpe),
            "expected_max_sharpe": _safe_float(cs.dsr.expected_max_sharpe),
            "sharpe_variance": _safe_float(cs.dsr.sharpe_variance),
            "z": _safe_float(cs.dsr.z),
            "dsr": _safe_float(cs.dsr.dsr),
        }
        for cs in decision.candidate_scores
    ]
    payload["sensitivity_score"] = [
        {
            "trial_id": cs.trial_id,
            "raw_score": _safe_float(cs.sensitivity.raw_score),
            "neighborhood_mean": _safe_float(cs.sensitivity.neighborhood_mean),
            "neighborhood_std": _safe_float(cs.sensitivity.neighborhood_std),
            "robust_score": _safe_float(cs.sensitivity.robust_score),
            "neighbors_used": cs.sensitivity.neighbors_used,
        }
        for cs in decision.candidate_scores
    ]
    payload["would_have_picked"] = decision.would_have_picked_trial_id
    payload["selection_methodology"] = dict(decision.methodology)
    # Update final to the selection-layer pick (or None if rejected).
    if decision.status.value == "accepted" and decision.best_trial_id is not None:
        cv = original_best.get("cross_validation") or [None]
        if decision.best_trial_id < len(cv):
            payload["final"] = cv[decision.best_trial_id]
    else:
        payload["final"] = None
    return payload


def _record_reselection(manifest_path: Path, best_path: Path, decision: SelectionDecision) -> None:
    manifest = json.loads(manifest_path.read_text())
    history = list(manifest.get("reselection_history", []))
    history.append(
        {
            "best_path": str(best_path.name),
            "timestamp": best_path.stem.removeprefix("best_"),
            "status": decision.status.value,
            "pbo": decision.pbo.pbo,
            "pbo_threshold": decision.pbo_threshold,
            "robust_objective": decision.robust_objective,
            "force_override": decision.force_override,
        }
    )
    manifest["reselection_history"] = history
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


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
    "reselect",
]
