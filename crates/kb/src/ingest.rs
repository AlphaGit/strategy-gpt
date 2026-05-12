//! Ingestion pipeline: source → chunks → embeddings → entities → relations,
//! writing to the graph and vector stores in a single transaction per source.

use std::path::Path;

use chrono::Utc;
use rusqlite::params;

use crate::chunk::chunk_text;
use crate::embed::Embedder;
use crate::error::KbError;
use crate::extract::Extractor;
use crate::graph::GraphStore;
use crate::source::SourceConfig;
use crate::store::Store;
use crate::vector::{ChunkInsert, VectorStore};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct IngestOutcome {
    pub source_id: String,
    pub chunks_written: usize,
    pub nodes_written: usize,
    pub edges_written: usize,
    pub content_hash: String,
}

pub struct IngestionPipeline<'a, E: Embedder, X: Extractor> {
    pub embedder: &'a E,
    pub extractor: &'a X,
}

impl<'a, E: Embedder, X: Extractor> IngestionPipeline<'a, E, X> {
    pub fn new(embedder: &'a E, extractor: &'a X) -> Self {
        Self {
            embedder,
            extractor,
        }
    }

    pub fn ingest_source(
        &self,
        store: &mut Store,
        cfg: &SourceConfig,
        text: &str,
    ) -> Result<IngestOutcome, KbError> {
        let content_hash = blake3::hash(text.as_bytes()).to_hex().to_string();
        let now = Utc::now().to_rfc3339();
        // Upsert source row and Source node (provenance target).
        store.conn.execute(
            "INSERT INTO sources (id, kind, title, author, year, path, section, ingested_at, content_hash) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) \
             ON CONFLICT(id) DO UPDATE SET \
                kind = excluded.kind, \
                title = excluded.title, \
                author = excluded.author, \
                year = excluded.year, \
                path = excluded.path, \
                section = excluded.section, \
                ingested_at = excluded.ingested_at, \
                content_hash = excluded.content_hash",
            params![
                cfg.id,
                serde_json::to_string(&cfg.kind)?.trim_matches('"'),
                cfg.title,
                cfg.author,
                cfg.year,
                cfg.path,
                cfg.section,
                now,
                content_hash,
            ],
        )?;
        store.upsert_source_node(&cfg.id, &cfg.title)?;
        // Re-ingestion drops chunks/embeddings and non-Source nodes, then
        // re-emits them. Source node identity is stable.
        store.purge_source(&cfg.id)?;
        let chunks = chunk_text(text, cfg.chunk_size, cfg.chunk_overlap);
        let mut chunks_written = 0;
        let mut nodes_written = 0;
        let mut edges_written = 0;
        for chunk in &chunks {
            let chunk_id = format!("{}::chunk::{}", cfg.id, chunk.ord);
            VectorStore::insert_chunk(
                store,
                ChunkInsert {
                    id: &chunk_id,
                    source_id: &cfg.id,
                    ord: chunk.ord,
                    text: &chunk.text,
                    page: None,
                    section: cfg.section.as_deref(),
                },
            )?;
            let vec = self.embedder.embed(&chunk.text)?;
            if vec.len() != self.embedder.dim() {
                return Err(KbError::EmbeddingDim {
                    expected: self.embedder.dim(),
                    got: vec.len(),
                });
            }
            VectorStore::insert_embedding(store, &chunk_id, &vec)?;
            chunks_written += 1;
            let facts = self.extractor.extract(&cfg.id, &chunk_id, &chunk.text)?;
            for node in &facts.nodes {
                GraphStore::insert_node(store, node)?;
                nodes_written += 1;
            }
            for edge in &facts.relations {
                GraphStore::insert_edge(store, edge)?;
                edges_written += 1;
            }
        }
        Ok(IngestOutcome {
            source_id: cfg.id.clone(),
            chunks_written,
            nodes_written,
            edges_written,
            content_hash,
        })
    }

    /// Read text from `cfg.path` (resolved relative to `base_dir` if relative)
    /// and ingest. Convenience wrapper for the curated source-list flow.
    pub fn ingest_from_disk(
        &self,
        store: &mut Store,
        cfg: &SourceConfig,
        base_dir: &Path,
    ) -> Result<IngestOutcome, KbError> {
        let path = Path::new(&cfg.path);
        let resolved = if path.is_absolute() {
            path.to_path_buf()
        } else {
            base_dir.join(path)
        };
        let text = std::fs::read_to_string(&resolved)?;
        self.ingest_source(store, cfg, &text)
    }
}
