//! Knowledge base: hybrid graph + vector retrieval over curated financial
//! resources.
//!
//! The spec ([`knowledge-base`](../../../openspec/specs/knowledge-base/spec.md))
//! defines the retrieval contract. The current implementation is an
//! embedded SQLite-backed store; the [`graph::GraphStore`] /
//! [`vector::VectorStore`] traits are the swap points if the storage
//! choice changes.

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
