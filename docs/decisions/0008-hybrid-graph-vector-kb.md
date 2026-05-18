# 0008 — Hybrid graph + vector knowledge base over SQLite

## Context

The hypothesis loop's `kb_query` node retrieves curated material — books, papers, prior decisions — and surfaces it as citations attached to generated hypotheses. Two retrieval modes are useful: semantic similarity (vector) for "what does the corpus say about X" and structural traversal (graph) for "what does Pardo chapter 9 reference, and what cites it." Pure vector retrieval misses the structural seams; pure graph retrieval misses unstructured similarity.

## Decision

The knowledge base provides **hybrid graph + vector retrieval** over a **SQLite-backed store**. The retrieval contract — `retrieve(query, k, modes)` returning typed `Citation` records — is the load-bearing interface; the storage choice (SQLite for the relational + sqlite-vss / pgvector-style extension for vectors) is internal and replaceable.

## Consequences

- One ingestion pipeline, one retrieval surface, hybrid scoring inside the contract.
- SQLite backing keeps the operational story consistent with the ledger ([0007](0007-sqlite-parquet-ledger.md)) — copy a directory.
- Vector index quality is bounded by what an in-SQLite extension can do; for the corpus size today this is fine.
- A future move to pgvector, FAISS, or a dedicated vector DB does not change downstream callers — only the storage layer.

## Alternatives Considered

- **Pure vector store (FAISS / Chroma).** Lose the graph traversals the curators want.
- **Pure graph store (Neo4j).** Lose the similarity ergonomics and add a heavyweight service.
- **No KB / live LLM retrieval.** Forecloses citation hygiene and offline-replay determinism.

## Status

accepted
