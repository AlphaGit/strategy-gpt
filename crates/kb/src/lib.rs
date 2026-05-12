//! Knowledge base: hybrid graph + vector retrieval over curated financial
//! resources.
//!
//! The spec ([`knowledge-base`](../../../openspec/changes/rewrite-architecture/specs/knowledge-base/spec.md))
//! names Kuzu and LanceDB as the underlying stores. v1 ships an embedded
//! SQLite-backed implementation that preserves the same retrieval contract and
//! schema; swapping the backing store to real Kuzu + LanceDB is a localized
//! refactor against the [`graph::GraphStore`] / [`vector::VectorStore`] traits.

pub mod chunk;
pub mod client;
pub mod embed;
pub mod error;
pub mod extract;
pub mod graph;
pub mod ingest;
pub mod retrieve;
pub mod schema;
pub mod source;
pub mod store;
pub mod vector;

pub use client::KnowledgeBase;
pub use embed::{Embedder, HashEmbedder};
pub use error::KbError;
pub use extract::{Extractor, KeywordExtractor};
pub use ingest::{IngestOutcome, IngestionPipeline};
pub use retrieve::{Provenance, RetrievalResult, RetrievedItem};
pub use schema::{EdgeKind, NodeKind, NodeRecord, RelationRecord};
pub use source::{SourceConfig, SourceList};
