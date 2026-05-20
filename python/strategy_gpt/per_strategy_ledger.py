"""Per-strategy hypothesis ledger.

Implements the storage layout decided in
:doc:`../docs/decisions/0017-per-strategy-storage-layout` and the spec
``experiment-ledger::per-strategy-storage-layout`` /
``content-addressed-source-blob-persistence`` /
``baseline-best-cache-per-strategy``.

Directory layout::

    ledger/strategies/<strategy_name>/
      hypothesis_records.parquet
      decision_records.parquet
      baseline/
        files_manifest.json
        best.json
      sources/
        <files_set_hash>/{Cargo.toml, src/lib.rs, params_schema.json, ...}
      responses/
        <decision_id>/{stage1_idea.md, stage2_commitments.md,
                       stage3_files.md, repair_<n>.md}

The module is intentionally Python-only — the native ledger continues to
own the run-level SQLite store (``runs.db``); the hypothesis loop's
per-strategy artifacts layer on top.

Strategy identity is the strategy crate ``name``; the orchestrator passes
it in when constructing :class:`PerStrategyLedger`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Records — richer than the legacy mirrors in ``types.py``. These are the
# native shape for hypothesize records under the new storage layout.
# ---------------------------------------------------------------------------


class AddedParam(BaseModel):
    """A new parameter introduced by a candidate."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str  # one of "f64", "i64", "bool", "string"
    min: float | None = None
    max: float | None = None
    default: Any


class ParamIntent(BaseModel):
    """LLM-stated intent over the candidate's parameter surface.

    ``added`` is the LLM's contribution: new parameters with explicit
    ``(min, max, default)`` bounds. ``kept`` lists parameter names the
    candidate inherits unchanged from the baseline (bounds taken from the
    experiment-spec). ``removed`` lists parameter names the candidate
    drops — they must be absent from the mini-optimize search space.
    """

    model_config = ConfigDict(frozen=True)

    added: list[AddedParam] = Field(default_factory=list)
    kept: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class GuardConstraint(BaseModel):
    """One guard the candidate promises NOT to break."""

    model_config = ConfigDict(frozen=True)

    metric: str
    direction: str  # "lte", "gte", "eq", "lt", "gt"
    delta_vs_baseline: float | None = None
    factor: float | None = None


class FalsificationScope(BaseModel):
    """Scope under which the falsification claim must hold.

    ``kind`` is one of ``aggregate``, ``regime``, ``fold``, ``window``.
    The remaining fields are populated as relevant to ``kind``; the
    orchestrator interprets them when computing the comparative verdict.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = "aggregate"
    regime: str | None = None
    fold: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None


class FalsificationPrimary(BaseModel):
    """Primary claim the candidate stands or falls on."""

    model_config = ConfigDict(frozen=True)

    metric: str
    direction: str  # "gt", "gte", "lt", "lte"
    delta_vs_baseline: float
    scope: FalsificationScope = Field(default_factory=FalsificationScope)


class Falsification(BaseModel):
    """Comparative falsification block: primary claim plus guard
    constraints. Verified by the tester against the baseline-best
    result on the same dataset_manifest."""

    model_config = ConfigDict(frozen=True)

    primary: FalsificationPrimary
    guard_constraints: list[GuardConstraint] = Field(default_factory=list)


class StageResponses(BaseModel):
    """Hashes of the three stage emission blobs co-located under the
    per-decision response folder. ``stage1_hash`` / ``stage2_hash`` /
    ``stage3_hash`` are the BLAKE2b-256 hex digests of the corresponding
    markdown blob; the orchestrator persists the blobs themselves under
    ``responses/<decision_id>/``."""

    model_config = ConfigDict(frozen=True)

    stage1_hash: str
    stage2_hash: str
    stage3_hash: str


class HypothesisRecordV2(BaseModel):
    """Per-strategy hypothesis record.

    Mirrors the schema in ``hypothesis-loop::hypothesis-output-schema``.
    Notice the absence of ``runner_version`` — see ADR 0018.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    strategy: str
    candidate_name: str
    files_manifest: dict[str, str]  # path → content-addressed blob hash
    deleted_files: list[str] = Field(default_factory=list)
    baseline_files_hash: str
    param_intent: ParamIntent
    falsification: Falsification
    expected_lift_confidence: float = Field(ge=0.0, le=1.0)
    expected_side_effects: list[str] = Field(default_factory=list)
    rationale: str = Field(max_length=500)
    stage_responses: StageResponses
    kb_cites: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    @field_validator("rationale", mode="before")
    @classmethod
    def _truncate_rationale(cls, v: str) -> str:
        return v[:500] if isinstance(v, str) else v


class DecisionStage(BaseModel):
    """The point at which a decision was made.

    ``kind`` is one of ``accepted``, ``rejected``. For rejections,
    ``stage`` further identifies which check fired so subsequent
    iterations and operators can categorize at a glance.
    """

    model_config = ConfigDict(frozen=True)

    kind: str  # "accepted" | "rejected"
    stage: str | None = None  # cheap_critique, build, lint, schema, smoke,
    #                            mechanical_gate, verdict_critique, repair_exhausted


class RepairAttempt(BaseModel):
    """One repair attempt recorded under a decision's evidence chain."""

    model_config = ConfigDict(frozen=True)

    stage: str  # "stage1" | "stage2" | "stage3"
    attempt_index: int
    files_hash: str | None = None
    reject_kind: str
    feedback: str


class DecisionRecordV2(BaseModel):
    """Per-strategy decision record paired with a
    :class:`HypothesisRecordV2`."""

    model_config = ConfigDict(frozen=True)

    id: str
    hypothesis_id: str
    strategy: str
    outcome: DecisionStage
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    repair_attempts: list[RepairAttempt] = Field(default_factory=list)
    decided_at: datetime


# ---------------------------------------------------------------------------
# Parquet schemas. Records are serialized to JSON columns so the schema
# stays narrow even as record shape evolves; analytic queries unpack the
# JSON in DuckDB / polars.
# ---------------------------------------------------------------------------


_HYPOTHESIS_PARQUET_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("strategy", pa.string()),
        ("candidate_name", pa.string()),
        ("baseline_files_hash", pa.string()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("record_json", pa.string()),
    ]
)


_DECISION_PARQUET_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("hypothesis_id", pa.string()),
        ("strategy", pa.string()),
        ("kind", pa.string()),
        ("stage", pa.string()),
        ("decided_at", pa.timestamp("us", tz="UTC")),
        ("record_json", pa.string()),
    ]
)


# ---------------------------------------------------------------------------
# Stage names — used both for response-blob filenames and for the
# ``DecisionStage.stage`` enumeration.
# ---------------------------------------------------------------------------

STAGE1 = "stage1_idea"
STAGE2 = "stage2_commitments"
STAGE3 = "stage3_files"


def _blake2_hex(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def canonical_files_set_hash(files: Mapping[str, str]) -> str:
    """Deterministic content hash for a source-file bundle.

    Concatenates ``path\0content\0`` for each entry in path-sorted order
    and hashes the result with BLAKE2b-256. Stable across operating
    systems and Python versions; suitable as a content-addressed
    directory name."""
    hasher = hashlib.blake2b(digest_size=32)
    for path in sorted(files.keys()):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(files[path].encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


class PerStrategyLedger:
    """Read/write surface for one strategy's hypothesize history.

    Construct with the ledger root and the strategy name; the folder is
    created lazily on first write. The class is intentionally
    file-system-only — it does not touch the native SQLite ledger.
    """

    def __init__(self, root: Path | str, strategy: str) -> None:
        if not strategy:
            msg = "strategy name must not be empty"
            raise ValueError(msg)
        self._root = Path(root)
        self._strategy = strategy
        self._strategy_dir = self._root / "strategies" / strategy

    # ---- Paths --------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def strategy_dir(self) -> Path:
        return self._strategy_dir

    @property
    def hypotheses_path(self) -> Path:
        return self._strategy_dir / "hypothesis_records.parquet"

    @property
    def decisions_path(self) -> Path:
        return self._strategy_dir / "decision_records.parquet"

    @property
    def baseline_dir(self) -> Path:
        return self._strategy_dir / "baseline"

    @property
    def baseline_best_path(self) -> Path:
        return self.baseline_dir / "best.json"

    @property
    def baseline_files_manifest_path(self) -> Path:
        return self.baseline_dir / "files_manifest.json"

    @property
    def sources_dir(self) -> Path:
        return self._strategy_dir / "sources"

    @property
    def responses_dir(self) -> Path:
        return self._strategy_dir / "responses"

    def _ensure_strategy_dir(self) -> None:
        self._strategy_dir.mkdir(parents=True, exist_ok=True)

    # ---- Source blob storage (spec: content-addressed) ----------------

    def write_source_set(self, files: Mapping[str, str]) -> str:
        """Persist ``files`` under ``sources/<files_set_hash>/``.

        Returns the content hash slug. Identical bundles dedup naturally
        — when the target directory already exists, the call is a
        no-op. Path keys in ``files`` are relative to the strategy crate
        root (e.g. ``"src/lib.rs"``, ``"Cargo.toml"``,
        ``"params_schema.json"``).
        """
        digest = canonical_files_set_hash(files)
        target = self.sources_dir / digest
        if target.exists():
            return digest
        target.mkdir(parents=True, exist_ok=True)
        for rel_path, content in files.items():
            dest = target / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        return digest

    def read_source_set(self, files_set_hash: str) -> dict[str, str]:
        """Reconstruct a previously persisted source bundle as a path → content map."""
        root = self.sources_dir / files_set_hash
        if not root.is_dir():
            msg = f"source set {files_set_hash} not found under {self.sources_dir}"
            raise FileNotFoundError(msg)
        result: dict[str, str] = {}
        for path in _walk_files(root):
            rel = path.relative_to(root).as_posix()
            result[rel] = path.read_text(encoding="utf-8")
        return result

    # ---- Response blob storage ----------------------------------------

    def response_dir(self, decision_id: str) -> Path:
        return self.responses_dir / decision_id

    def write_response_blob(self, decision_id: str, stage: str, content: str) -> str:
        """Persist a stage emission blob; returns its BLAKE2b-256 hex hash.

        ``stage`` is one of :data:`STAGE1`, :data:`STAGE2`, :data:`STAGE3`,
        or a repair-attempt slug like ``"repair_0"``. The blob filename
        is ``<stage>.md``.
        """
        target_dir = self.response_dir(decision_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stage}.md"
        data = content.encode("utf-8")
        path.write_bytes(data)
        return _blake2_hex(data)

    def read_response_blob(self, decision_id: str, stage: str) -> str:
        path = self.response_dir(decision_id) / f"{stage}.md"
        return path.read_text(encoding="utf-8")

    # ---- Baseline cache (spec: baseline_best per strategy) -----------

    def read_baseline(self) -> dict[str, Any] | None:
        path = self.baseline_best_path
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def write_baseline(self, payload: Mapping[str, Any]) -> None:
        """Persist the baseline-best result for this strategy. Caller is
        responsible for ensuring ``payload`` includes the dataset
        manifest hash and the optimize seed so subsequent runs can
        verify they are reading the right baseline."""
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_best_path.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True, default=_json_default),
            encoding="utf-8",
        )

    def baseline_best(
        self,
        *,
        dataset_manifest_hash: str,
        compute_on_miss: Callable[[], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return the cached baseline-best for this strategy + dataset.

        On miss, invokes ``compute_on_miss`` (typically: run a baseline
        optimize pass) and persists its result before returning. When
        ``compute_on_miss`` is ``None`` and no cache exists, raises
        :class:`KeyError` so the orchestrator surfaces a clear error
        instead of guessing.

        The dataset manifest hash is verified: a cached baseline
        recorded against a different manifest is treated as a miss to
        avoid silently comparing against the wrong dataset.
        """
        cached = self.read_baseline()
        if cached is not None and cached.get("dataset_manifest_hash") == dataset_manifest_hash:
            return cached
        if compute_on_miss is None:
            msg = (
                f"no baseline-best cached for strategy={self._strategy!r}, "
                f"dataset_manifest_hash={dataset_manifest_hash!r}"
            )
            raise KeyError(msg)
        fresh = dict(compute_on_miss())
        fresh.setdefault("dataset_manifest_hash", dataset_manifest_hash)
        self.write_baseline(fresh)
        return fresh

    # ---- Hypothesis + decision parquet append --------------------------

    def record_hypothesis(self, record: HypothesisRecordV2) -> None:
        if record.strategy != self._strategy:
            msg = (
                f"record.strategy={record.strategy!r} does not match ledger "
                f"strategy={self._strategy!r}"
            )
            raise ValueError(msg)
        self._ensure_strategy_dir()
        table = pa.table(
            {
                "id": [record.id],
                "strategy": [record.strategy],
                "candidate_name": [record.candidate_name],
                "baseline_files_hash": [record.baseline_files_hash],
                "created_at": [_ensure_utc(record.created_at)],
                "record_json": [record.model_dump_json()],
            },
            schema=_HYPOTHESIS_PARQUET_SCHEMA,
        )
        _append_parquet(self.hypotheses_path, table, _HYPOTHESIS_PARQUET_SCHEMA)

    def record_decision(self, record: DecisionRecordV2) -> None:
        if record.strategy != self._strategy:
            msg = (
                f"record.strategy={record.strategy!r} does not match ledger "
                f"strategy={self._strategy!r}"
            )
            raise ValueError(msg)
        self._ensure_strategy_dir()
        table = pa.table(
            {
                "id": [record.id],
                "hypothesis_id": [record.hypothesis_id],
                "strategy": [record.strategy],
                "kind": [record.outcome.kind],
                "stage": [record.outcome.stage or ""],
                "decided_at": [_ensure_utc(record.decided_at)],
                "record_json": [record.model_dump_json()],
            },
            schema=_DECISION_PARQUET_SCHEMA,
        )
        _append_parquet(self.decisions_path, table, _DECISION_PARQUET_SCHEMA)

    def hypotheses_iter(self) -> Iterator[HypothesisRecordV2]:
        if not self.hypotheses_path.exists():
            return
        table = pq.read_table(self.hypotheses_path)
        for raw in table.column("record_json").to_pylist():
            yield HypothesisRecordV2.model_validate_json(raw)

    def decisions_iter(self) -> Iterator[DecisionRecordV2]:
        if not self.decisions_path.exists():
            return
        table = pq.read_table(self.decisions_path)
        for raw in table.column("record_json").to_pylist():
            yield DecisionRecordV2.model_validate_json(raw)

    def recent_decisions(self, limit: int = 50) -> list[DecisionRecordV2]:
        """Most recent decisions in descending ``decided_at`` order.

        Reads the whole parquet file once; the per-strategy history is
        small (low thousands) so a streaming reader is not warranted.
        """
        if not self.decisions_path.exists():
            return []
        table = pq.read_table(self.decisions_path)
        sorted_table = table.sort_by([("decided_at", "descending")]).slice(0, limit)
        return [
            DecisionRecordV2.model_validate_json(raw)
            for raw in sorted_table.column("record_json").to_pylist()
        ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _walk_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _json_default(o: Any) -> Any:  # noqa: ANN401  # pragma: no cover - trivial
    if isinstance(o, datetime):
        return _ensure_utc(o).isoformat()
    if isinstance(o, Path):
        return str(o)
    msg = f"cannot serialize {type(o).__name__} to JSON"
    raise TypeError(msg)


def _append_parquet(path: Path, table: pa.Table, schema: pa.Schema) -> None:
    """Append ``table`` to a parquet file at ``path``.

    Parquet has no native append; we round-trip through a read + concat
    when the file already exists. Per-strategy histories are small so
    the full rewrite cost is negligible (low thousands of rows).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pq.read_table(path)
        combined = pa.concat_tables([existing.cast(schema), table], promote_options="default")
    else:
        combined = table
    pq.write_table(combined, path)


__all__ = [
    "STAGE1",
    "STAGE2",
    "STAGE3",
    "AddedParam",
    "DecisionRecordV2",
    "DecisionStage",
    "Falsification",
    "FalsificationPrimary",
    "FalsificationScope",
    "GuardConstraint",
    "HypothesisRecordV2",
    "ParamIntent",
    "PerStrategyLedger",
    "RepairAttempt",
    "StageResponses",
    "canonical_files_set_hash",
]
