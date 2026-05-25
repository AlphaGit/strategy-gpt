"""Python wrapper around the PyO3 `DataGateway` class.

Adds typed inputs/outputs (pydantic models from `strategy_gpt.types`) on top
of the native module's JSON-string boundary. The wrapper does not cache
state; every call serializes its arguments and parses the response so each
operation is independently consistent with the underlying Rust types.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._native_shim import require_native
from .types import BarRequest, CacheMode, DatasetResponse


class CacheStats:
    """Lightweight DTO returned by :meth:`Gateway.cache_stats`."""

    __slots__ = ("blob_count", "total_bytes")

    def __init__(self, *, blob_count: int, total_bytes: int) -> None:
        self.blob_count = blob_count
        self.total_bytes = total_bytes

    def __repr__(self) -> str:
        return f"CacheStats(blob_count={self.blob_count}, total_bytes={self.total_bytes})"


class Gateway:
    """High-level wrapper over `strategy_gpt._native.gateway.DataGateway`."""

    def __init__(self, root: Path | str) -> None:
        native = require_native()
        self._gw = native.gateway.DataGateway(str(root))

    @property
    def root(self) -> str:
        result: str = self._gw.root()
        return result

    def register_csv_provider(self, name: str, base_dir: Path | str) -> None:
        """Register a CSV provider rooted at `base_dir` under `name`."""
        self._gw.register_csv_provider(name, str(base_dir))

    def register_yfinance_provider(
        self,
        name: str,
        base_url: str | None = None,
        timeout_secs: int | None = None,
    ) -> None:
        """Register a Yahoo Finance provider under `name`."""
        self._gw.register_yfinance_provider(name, base_url, timeout_secs)

    def fetch(self, request: BarRequest, mode: CacheMode = "prefer_cache") -> DatasetResponse:
        """Fetch a dataset for `request` honoring `mode`."""
        from .progress import phase  # noqa: PLC0415

        payload = request.model_dump_json()
        path = f"fetch.{request.provider}.download"
        with phase(path, msg=f"{request.symbol} {request.resolution}"):
            raw: str = self._gw.fetch(payload, mode)
        return DatasetResponse.model_validate_json(raw)

    def cache_stats(self) -> CacheStats:
        """Summarize the on-disk blob store: `blob_count` and `total_bytes`."""
        raw: str = self._gw.cache_stats()
        data: dict[str, Any] = json.loads(raw)
        return CacheStats(blob_count=int(data["blob_count"]), total_bytes=int(data["total_bytes"]))


__all__ = ["CacheStats", "Gateway"]
