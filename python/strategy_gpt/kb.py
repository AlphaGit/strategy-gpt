"""Python wrapper around the PyO3 `KnowledgeBase` class.

Mirrors the Rust API: `retrieve`, `add_source`, `add_source_from_text`, and
`reingest`. JSON-string boundary; results are returned as typed pydantic
models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._native_shim import require_native


class SourceConfig(BaseModel):
    """One entry in the curated source list (mirrors `kb::source::SourceConfig`)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["book", "paper", "article", "note"]
    title: str
    path: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    chunk_size: int = 600
    chunk_overlap: int = 80


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    page: int | None = None


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    name: str
    summary: str
    source_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


class RetrievedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    score: float
    graph_nodes: list[GraphNode]
    provenance: Provenance


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RetrievedItem]


class IngestOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    chunks_written: int
    nodes_written: int
    edges_written: int
    content_hash: str


class KnowledgeBase:
    """High-level wrapper over `strategy_gpt._native.kb.KnowledgeBase`."""

    def __init__(
        self,
        db_path: Path | str,
        base_dir: Path | str,
        *,
        embedding_dim: int = 64,
    ) -> None:
        native = require_native()
        self._kb = native.kb.KnowledgeBase(str(db_path), str(base_dir), embedding_dim)

    @property
    def base_dir(self) -> str:
        result: str = self._kb.base_dir()
        return result

    @property
    def source_count(self) -> int:
        result: int = self._kb.source_count()
        return result

    def retrieve(self, query: str, k: int = 10) -> RetrievalResult:
        raw: str = self._kb.retrieve(query, k)
        return RetrievalResult.model_validate_json(raw)

    def add_source(self, source: SourceConfig) -> IngestOutcome:
        raw: str = self._kb.add_source(source.model_dump_json())
        return IngestOutcome.model_validate_json(raw)

    def add_source_from_text(self, source: SourceConfig, text: str) -> IngestOutcome:
        raw: str = self._kb.add_source_from_text(source.model_dump_json(), text)
        return IngestOutcome.model_validate_json(raw)

    def reingest(self, source_list_toml: str) -> list[IngestOutcome]:
        raw: str = self._kb.reingest(source_list_toml)
        return [IngestOutcome.model_validate(item) for item in json.loads(raw)]


__all__ = [
    "GraphNode",
    "IngestOutcome",
    "KnowledgeBase",
    "Provenance",
    "RetrievalResult",
    "RetrievedItem",
    "SourceConfig",
]
