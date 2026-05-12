//! Hybrid retrieval: vector top-k → graph neighborhood expansion → re-rank.
//!
//! Spec scenario `hybrid-retrieval-call`: a `retrieve(query, k)` call must run
//! vector top-k against the vector store, expand the resulting source's
//! neighborhood in the graph, re-rank, and return a unified result set with
//! provenance.

use rusqlite::params;
use serde::{Deserialize, Serialize};

use crate::error::KbError;
use crate::graph::GraphStore;
use crate::schema::NodeRecord;
use crate::store::Store;
use crate::vector::{ChunkRecord, VectorStore};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Provenance {
    pub source_id: String,
    pub title: String,
    pub author: Option<String>,
    pub year: Option<i32>,
    pub section: Option<String>,
    pub page: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievedItem {
    pub chunk_id: String,
    pub text: String,
    pub score: f32,
    pub graph_nodes: Vec<NodeRecord>,
    pub provenance: Provenance,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievalResult {
    pub items: Vec<RetrievedItem>,
}

#[derive(Debug, Clone)]
pub struct RetrievalConfig {
    pub k: usize,
    pub vector_pool: usize,
    pub graph_hops: usize,
    /// Weight applied to vector score in the final re-rank score.
    pub vector_weight: f32,
    /// Weight applied to graph evidence (1.0 when ≥1 graph node found, 0 else).
    pub graph_weight: f32,
}

impl Default for RetrievalConfig {
    fn default() -> Self {
        Self {
            k: 10,
            vector_pool: 20,
            graph_hops: 1,
            vector_weight: 0.85,
            graph_weight: 0.15,
        }
    }
}

fn fetch_provenance(store: &Store, chunk: &ChunkRecord) -> Result<Provenance, KbError> {
    let mut stmt = store
        .conn
        .prepare("SELECT title, author, year, section FROM sources WHERE id = ?1")?;
    let mut rows = stmt.query(params![chunk.source_id])?;
    let (title, author, year, default_section): (
        String,
        Option<String>,
        Option<i32>,
        Option<String>,
    ) = if let Some(row) = rows.next()? {
        (row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)
    } else {
        return Err(KbError::SourceNotFound(chunk.source_id.clone()));
    };
    Ok(Provenance {
        source_id: chunk.source_id.clone(),
        title,
        author,
        year,
        section: chunk.section.clone().or(default_section),
        page: chunk.page,
    })
}

pub fn retrieve(
    store: &Store,
    query_embedding: &[f32],
    cfg: &RetrievalConfig,
) -> Result<RetrievalResult, KbError> {
    let pool = cfg.vector_pool.max(cfg.k);
    let hits = VectorStore::top_k(store, query_embedding, pool)?;
    let mut items: Vec<RetrievedItem> = Vec::with_capacity(hits.len());
    for hit in hits {
        let provenance = fetch_provenance(store, &hit.chunk)?;
        // Graph expansion seeded by all non-Source nodes whose provenance is
        // this chunk's source. Spec: every retrieval item carries graph nodes
        // with provenance, and Hypothesis Loop can cite them.
        let mut seed_ids: Vec<String> = store
            .nodes_for_source(&hit.chunk.source_id)?
            .into_iter()
            .map(|n| n.id)
            .collect();
        if seed_ids.is_empty() {
            // No extracted nodes for this source — keep Source node itself as
            // a citation anchor.
            seed_ids.push(hit.chunk.source_id.clone());
        }
        let nodes = store.neighborhood(&seed_ids, cfg.graph_hops)?;
        let graph_signal = if nodes.is_empty() { 0.0_f32 } else { 1.0_f32 };
        let score = cfg.vector_weight * hit.score + cfg.graph_weight * graph_signal;
        items.push(RetrievedItem {
            chunk_id: hit.chunk.id.clone(),
            text: hit.chunk.text.clone(),
            score,
            graph_nodes: nodes,
            provenance,
        });
    }
    items.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    items.truncate(cfg.k);
    Ok(RetrievalResult { items })
}
