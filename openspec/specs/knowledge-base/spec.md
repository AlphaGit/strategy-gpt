# Spec: knowledge-base

## Purpose

Hybrid graph + vector knowledge store (Kuzu + LanceDB) populated by a curated ingestion pipeline over investment books, papers, and articles. Provides citation-friendly retrieval so the Hypothesis Loop and Optimizer can ground reasoning in source-attributed concepts, indicators, regimes, models, and techniques.

## Requirements

### Requirement: Hybrid Kuzu (graph) + LanceDB (vector) store

The Knowledge Base SHALL store relations in Kuzu and dense embeddings in LanceDB, both as embedded local stores. The KB MUST present a single retrieval API to clients regardless of which underlying store serves a given part of a query.

#### Scenario: Hybrid retrieval call

- **WHEN** a client requests `retrieve(query, k=10)`
- **THEN** the KB runs vector top-k against LanceDB, expands the resulting nodes' neighborhoods in Kuzu, re-ranks, and returns a unified result set

### Requirement: Graph schema for financial knowledge

The Kuzu schema SHALL include node types `Concept`, `Indicator`, `Regime`, `Model`, `Technique`, `Source` and relation types `IMPLEMENTS`, `CONTRADICTS`, `REQUIRES`, `GENERALIZES`, `CITES`, `EMPIRICAL_SUPPORT`, `FAILS_IN_REGIME`. Every node MUST carry a `provenance` reference to a `Source` node.

#### Scenario: Node has provenance

- **WHEN** any non-Source node is read
- **THEN** the node's provenance edges enumerate at least one `Source` node

### Requirement: Curated ingestion pipeline

The KB SHALL be populated by a curated ingestion pipeline that accepts a human-approved list of resources (books, papers, articles), chunks them, embeds chunks, runs an LLM-based entity and relation extractor, and writes results into Kuzu and LanceDB. The pipeline MUST NOT auto-ingest from open scrapes.

#### Scenario: Adding a new source

- **WHEN** an operator adds a book to the curated source list and runs ingestion
- **THEN** chunks, embeddings, extracted entities, and relations are inserted with provenance pointing to the new `Source` node

### Requirement: Citation-friendly retrieval

Retrieval results SHALL include source provenance (book, page or paper, section) for every returned chunk and graph node. Clients MUST be able to surface citations to downstream consumers (e.g., the Hypothesis Loop and Optimizer rationale generator).

#### Scenario: Hypothesis loop receives citations

- **WHEN** the Hypothesis Loop's `kb_query` node calls retrieve
- **THEN** every returned item includes provenance fields sufficient for the loop to attach a citation to a generated hypothesis

### Requirement: Embedded operation, no external services

The KB SHALL run entirely from local files; both Kuzu and LanceDB MUST be embedded. The KB MUST NOT require a long-running server process or network connectivity to operate.

#### Scenario: Operating without network

- **WHEN** the host has no network connectivity
- **THEN** the KB serves retrieval requests from local files without error

### Requirement: Prior-decision-aware retrieval filter

The Knowledge Base SHALL expose a retrieval pipeline that lets a client apply a deterministic post-retrieval filter consuming a list of prior decisions. The filter MUST be able to (a) drop or score-discount chunks whose `(source, locator)` appears in the `kb_cites` of any rejected prior decision, and (b) boost chunks whose `(source, locator)` appears in the `kb_cites` of any accepted prior decision. The filter MUST be implemented client-side over the KB's standard retrieval results; the KB client interface MUST NOT require knowledge of decision history.

#### Scenario: Recycled rejected chunk is suppressed

- **WHEN** a client retrieves with a prior-decision list whose rejected entries cite the chunk at `(book_x, p.42)`
- **THEN** the filter drops or score-discounts that chunk before returning the result set

#### Scenario: Accepted-cite chunk is uplifted

- **WHEN** a client retrieves with a prior-decision list whose accepted entries cite the chunk at `(paper_y, sec.3)`
- **THEN** the filter raises that chunk's effective score so it ranks higher in the returned set

#### Scenario: KB client remains decision-agnostic

- **WHEN** the KB's retrieve API is called without a prior-decision list
- **THEN** the KB returns the standard score-ranked top-k result set with no filter applied
