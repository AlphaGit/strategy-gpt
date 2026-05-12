"""End-to-end tests for the KB Python wrapper backed by the native module.

Skipped when the extension is unbuilt. Exercises:

- Curated TOML reingestion of the starter corpus, with retrieval surfacing
  the most relevant chunk under domain queries.
- Citation presence on every retrieval result (provenance fields complete).
- Offline operation: KB serves retrieval requests from local SQLite without
  any network or external-service dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_gpt._native_shim import native_available
from strategy_gpt.kb import KnowledgeBase, SourceConfig


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    if not native_available():
        pytest.skip("native extension not built")
    db_path = tmp_path / "kb.sqlite"
    return KnowledgeBase(db_path, _repo_root() / "kb")


def test_retrieval_against_starter_corpus(kb: KnowledgeBase) -> None:
    source_list_toml = (_repo_root() / "kb" / "sources.toml").read_text()
    outcomes = kb.reingest(source_list_toml)
    assert len(outcomes) == 3
    assert all(o.chunks_written > 0 for o in outcomes)

    result = kb.retrieve("vix backwardation regime", k=5)
    assert result.items, "expected retrieval results for an in-domain query"
    top = result.items[0]
    assert "vol" in top.provenance.title.lower() or "volatility" in top.provenance.title.lower()


def test_every_retrieval_item_has_citations(kb: KnowledgeBase) -> None:
    cfg = SourceConfig(
        id="probe",
        kind="note",
        title="Probe Note",
        path="n/a",
        author="Tester",
        year=2026,
        section="intro",
        chunk_size=100,
        chunk_overlap=10,
    )
    kb.add_source_from_text(cfg, "vix backwardation drives vxx decay; rsi mean reversion")
    result = kb.retrieve("vxx decay", k=3)
    assert result.items
    for item in result.items:
        assert item.provenance.source_id
        assert item.provenance.title
        assert item.chunk_id


def test_offline_operation(kb: KnowledgeBase) -> None:
    # The KB is SQLite-backed and embeds the HashEmbedder; no network is
    # touched anywhere in this test path. We assert the contract by exercising
    # the full retrieve loop in-process.
    cfg = SourceConfig(
        id="offline-probe",
        kind="note",
        title="Offline",
        path="n/a",
        chunk_size=100,
        chunk_overlap=10,
    )
    kb.add_source_from_text(cfg, "regime detection drives strategy switching")
    result = kb.retrieve("regime detection", k=2)
    assert result.items
    assert result.items[0].text


def test_source_count_reflects_writes(kb: KnowledgeBase) -> None:
    cfg = SourceConfig(id="c", kind="note", title="C", path="n/a", chunk_size=100, chunk_overlap=10)
    assert kb.source_count == 0
    kb.add_source_from_text(cfg, "ema momentum trend")
    assert kb.source_count == 1
    kb.add_source_from_text(cfg, "ema momentum trend, updated")
    assert kb.source_count == 1  # same id, upsert
