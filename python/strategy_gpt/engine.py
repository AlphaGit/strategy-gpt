"""Python wrapper around the PyO3 `Engine` class.

Surface (mirrors `crates/py-bindings/src/engine_mod.rs`):

- :meth:`Engine.submit_batch` — load a strategy artifact, schedule a batch.
- :meth:`Engine.poll` — read job state as a typed :class:`JobStatus`.
- :meth:`Engine.cancel` — request cooperative cancellation.
- :meth:`Engine.drop_handle` — discard a finished/cancelled handle.

The `BatchSpec` and `BacktestResult` shapes are not yet mirrored as pydantic
types in :mod:`strategy_gpt.types`; this wrapper accepts/returns them as
JSON-serializable dicts to match the native module's boundary. Typed mirrors
land if and when a consumer needs them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from ._native_shim import require_native
from .types import Bar

_BARS_ADAPTER: TypeAdapter[list[Bar]] = TypeAdapter(list[Bar])

JobStatusKind = Literal["running", "completed", "failed", "cancelled"]


class JobStatus(BaseModel):
    """Typed view of the JSON payload returned by :meth:`Engine.poll`.

    ``results`` is only present when ``status == "completed"``; ``error`` is
    only present when ``status == "failed"``.
    """

    model_config = ConfigDict(frozen=True)

    status: JobStatusKind
    results: list[dict[str, Any]] | None = None
    error: str | None = None


class Engine:
    """High-level wrapper over `strategy_gpt._native.engine.Engine`.

    ``worker_path`` is the filesystem path to the compiled ``engine-worker``
    binary the underlying coordinator spawns for every run (one subprocess
    per :class:`~strategy_gpt.types.RunSpec` in a batch). Strategy execution
    happens entirely inside the worker subprocess; the orchestrator never
    loads strategy code in-process.

    ``time_cap_secs`` and ``mem_cap_bytes`` are forwarded to the coordinator
    as per-run resource caps. The memory cap is best-effort on macOS (see
    ``crates/engine/src/bin/engine_worker.rs`` for the cross-platform
    setrlimit notes).
    """

    def __init__(
        self,
        worker_path: Path | str,
        *,
        time_cap_secs: float | None = None,
        mem_cap_bytes: int | None = None,
    ) -> None:
        native = require_native()
        self._engine = native.engine.Engine(
            str(worker_path),
            time_cap_secs,
            mem_cap_bytes,
        )

    def submit_batch(
        self,
        artifact_path: Path | str,
        bars: list[Bar],
        spec: dict[str, Any],
        dataset_manifest: str,
    ) -> str:
        """Submit a batch and return an opaque handle id.

        ``spec`` is a `BatchSpec` dict matching the Rust serde shape
        (``strategy``, ``dataset``, ``runs``, ``engine``, ``parallelism``).
        """
        bars_json = _BARS_ADAPTER.dump_json(bars).decode()
        spec_json = json.dumps(spec)
        handle: str = self._engine.submit_batch(
            str(artifact_path), bars_json, spec_json, dataset_manifest
        )
        return handle

    def poll(self, handle: str) -> JobStatus:
        """Return the current state of `handle` as a :class:`JobStatus`."""
        raw: str = self._engine.poll(handle)
        return JobStatus.model_validate_json(raw)

    def cancel(self, handle: str) -> bool:
        """Request cooperative cancellation; returns whether the job was running."""
        result: bool = self._engine.cancel(handle)
        return result

    def drop_handle(self, handle: str) -> bool:
        """Release `handle`; returns whether the handle existed."""
        result: bool = self._engine.drop_handle(handle)
        return result


__all__ = ["Engine", "JobStatus", "JobStatusKind"]
