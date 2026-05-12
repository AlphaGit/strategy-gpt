//! High-level KB facade. Wraps a [`Store`], an [`Embedder`], and an
//! [`Extractor`] into the single API surface the orchestrator consumes:
//! `retrieve`, `add_source`, `reingest`. Matches PyO3 binding shape in
//! `py-bindings/src/kb_mod.rs`.

use std::path::{Path, PathBuf};

use crate::embed::Embedder;
use crate::error::KbError;
use crate::extract::Extractor;
use crate::ingest::{IngestOutcome, IngestionPipeline};
use crate::retrieve::{retrieve, RetrievalConfig, RetrievalResult};
use crate::source::{SourceConfig, SourceList};
use crate::store::Store;

pub struct KnowledgeBase<E: Embedder, X: Extractor> {
    pub store: Store,
    pub embedder: E,
    pub extractor: X,
    pub base_dir: PathBuf,
}

impl<E: Embedder, X: Extractor> KnowledgeBase<E, X> {
    pub fn open(
        db_path: &Path,
        base_dir: &Path,
        embedder: E,
        extractor: X,
    ) -> Result<Self, KbError> {
        let store = Store::open(db_path)?;
        Ok(Self {
            store,
            embedder,
            extractor,
            base_dir: base_dir.to_path_buf(),
        })
    }

    pub fn open_in_memory(base_dir: &Path, embedder: E, extractor: X) -> Result<Self, KbError> {
        let store = Store::open_in_memory()?;
        Ok(Self {
            store,
            embedder,
            extractor,
            base_dir: base_dir.to_path_buf(),
        })
    }

    pub fn retrieve(&self, query: &str, k: usize) -> Result<RetrievalResult, KbError> {
        let cfg = RetrievalConfig {
            k,
            ..RetrievalConfig::default()
        };
        let embedding = self.embedder.embed(query)?;
        retrieve(&self.store, &embedding, &cfg)
    }

    pub fn retrieve_with(
        &self,
        query: &str,
        cfg: &RetrievalConfig,
    ) -> Result<RetrievalResult, KbError> {
        let embedding = self.embedder.embed(query)?;
        retrieve(&self.store, &embedding, cfg)
    }

    /// Add a single source by ingesting it from disk. Replaces any prior
    /// version of the same source id.
    pub fn add_source(&mut self, cfg: &SourceConfig) -> Result<IngestOutcome, KbError> {
        let pipeline = IngestionPipeline::new(&self.embedder, &self.extractor);
        pipeline.ingest_from_disk(&mut self.store, cfg, &self.base_dir)
    }

    /// Add a source from a pre-supplied text blob (skips disk read; used by
    /// tests and orchestrator-side ingestion adapters).
    pub fn add_source_from_text(
        &mut self,
        cfg: &SourceConfig,
        text: &str,
    ) -> Result<IngestOutcome, KbError> {
        let pipeline = IngestionPipeline::new(&self.embedder, &self.extractor);
        pipeline.ingest_source(&mut self.store, cfg, text)
    }

    /// Re-ingest every source listed in `list`. Source ids preserved across
    /// runs; chunks/embeddings/non-Source nodes are dropped and rewritten.
    pub fn reingest(&mut self, list: &SourceList) -> Result<Vec<IngestOutcome>, KbError> {
        let mut out = Vec::with_capacity(list.source.len());
        for cfg in &list.source {
            out.push(self.add_source(cfg)?);
        }
        Ok(out)
    }
}
