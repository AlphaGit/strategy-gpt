//! Vector store (v1: SQLite-backed brute-force cosine; future: lancedb).
//!
//! Each chunk has a single row in `embeddings`. Top-k retrieval scans the full
//! table — fine at curated-KB scale (low thousands of chunks) and keeps the v1
//! footprint minimal. Swap to a LanceDB collection at the trait boundary
//! (`VectorStore::top_k`) when scale justifies the dependency cost.

use rusqlite::params;

use crate::error::KbError;
use crate::store::Store;

#[derive(Debug, Clone)]
pub struct ChunkInsert<'a> {
    pub id: &'a str,
    pub source_id: &'a str,
    pub ord: usize,
    pub text: &'a str,
    pub page: Option<i64>,
    pub section: Option<&'a str>,
}

#[derive(Debug, Clone)]
pub struct ChunkRecord {
    pub id: String,
    pub source_id: String,
    pub ord: i64,
    pub text: String,
    pub page: Option<i64>,
    pub section: Option<String>,
}

#[derive(Debug, Clone)]
pub struct VectorHit {
    pub chunk: ChunkRecord,
    pub score: f32,
}

pub trait VectorStore {
    fn insert_chunk(&mut self, chunk: ChunkInsert<'_>) -> Result<(), KbError>;
    fn insert_embedding(&mut self, chunk_id: &str, vec: &[f32]) -> Result<(), KbError>;
    fn get_chunk(&self, chunk_id: &str) -> Result<Option<ChunkRecord>, KbError>;
    fn top_k(&self, query: &[f32], k: usize) -> Result<Vec<VectorHit>, KbError>;
}

fn encode_vec(vec: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for v in vec {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

fn decode_vec(bytes: &[u8]) -> Vec<f32> {
    let mut out = Vec::with_capacity(bytes.len() / 4);
    let mut i = 0;
    while i + 4 <= bytes.len() {
        let arr = [bytes[i], bytes[i + 1], bytes[i + 2], bytes[i + 3]];
        out.push(f32::from_le_bytes(arr));
        i += 4;
    }
    out
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let mut dot = 0.0_f32;
    let mut na = 0.0_f32;
    let mut nb = 0.0_f32;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        na += x * x;
        nb += y * y;
    }
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    dot / (na.sqrt() * nb.sqrt())
}

impl VectorStore for Store {
    fn insert_chunk(&mut self, chunk: ChunkInsert<'_>) -> Result<(), KbError> {
        self.conn.execute(
            "INSERT INTO chunks (id, source_id, ord, text, page, section) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
             ON CONFLICT(id) DO UPDATE SET \
                source_id = excluded.source_id, \
                ord = excluded.ord, \
                text = excluded.text, \
                page = excluded.page, \
                section = excluded.section",
            params![
                chunk.id,
                chunk.source_id,
                chunk.ord as i64,
                chunk.text,
                chunk.page,
                chunk.section,
            ],
        )?;
        Ok(())
    }

    fn insert_embedding(&mut self, chunk_id: &str, vec: &[f32]) -> Result<(), KbError> {
        let blob = encode_vec(vec);
        self.conn.execute(
            "INSERT INTO embeddings (chunk_id, dim, vec) VALUES (?1, ?2, ?3) \
             ON CONFLICT(chunk_id) DO UPDATE SET dim = excluded.dim, vec = excluded.vec",
            params![chunk_id, vec.len() as i64, blob],
        )?;
        Ok(())
    }

    fn get_chunk(&self, chunk_id: &str) -> Result<Option<ChunkRecord>, KbError> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, source_id, ord, text, page, section FROM chunks WHERE id = ?1")?;
        let mut rows = stmt.query(params![chunk_id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(ChunkRecord {
                id: row.get(0)?,
                source_id: row.get(1)?,
                ord: row.get(2)?,
                text: row.get(3)?,
                page: row.get(4)?,
                section: row.get(5)?,
            }))
        } else {
            Ok(None)
        }
    }

    fn top_k(&self, query: &[f32], k: usize) -> Result<Vec<VectorHit>, KbError> {
        let mut stmt = self.conn.prepare(
            "SELECT c.id, c.source_id, c.ord, c.text, c.page, c.section, e.vec \
             FROM embeddings e JOIN chunks c ON c.id = e.chunk_id",
        )?;
        let mut rows = stmt.query([])?;
        let mut hits: Vec<VectorHit> = Vec::new();
        while let Some(row) = rows.next()? {
            let blob: Vec<u8> = row.get(6)?;
            let vec = decode_vec(&blob);
            let score = cosine(query, &vec);
            hits.push(VectorHit {
                chunk: ChunkRecord {
                    id: row.get(0)?,
                    source_id: row.get(1)?,
                    ord: row.get(2)?,
                    text: row.get(3)?,
                    page: row.get(4)?,
                    section: row.get(5)?,
                },
                score,
            });
        }
        hits.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        hits.truncate(k);
        Ok(hits)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;

    fn insert_source(store: &mut Store, id: &str) {
        store
            .conn
            .execute(
                "INSERT INTO sources (id, kind, title, path, ingested_at, content_hash) \
                 VALUES (?1, 'paper', 'T', 'p', '2026-01-01T00:00:00Z', 'h')",
                params![id],
            )
            .unwrap();
    }

    #[test]
    fn cosine_top_k_orders_by_similarity() {
        let mut store = Store::open_in_memory().unwrap();
        insert_source(&mut store, "s1");
        store
            .insert_chunk(ChunkInsert {
                id: "c1",
                source_id: "s1",
                ord: 0,
                text: "alpha",
                page: None,
                section: None,
            })
            .unwrap();
        store
            .insert_chunk(ChunkInsert {
                id: "c2",
                source_id: "s1",
                ord: 1,
                text: "beta",
                page: None,
                section: None,
            })
            .unwrap();
        store.insert_embedding("c1", &[1.0, 0.0, 0.0]).unwrap();
        store.insert_embedding("c2", &[0.0, 1.0, 0.0]).unwrap();

        let hits = store.top_k(&[1.0, 0.1, 0.0], 2).unwrap();
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].chunk.id, "c1");
        assert_eq!(hits[1].chunk.id, "c2");
        assert!(hits[0].score > hits[1].score);
    }
}
