## ADDED Requirements

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
