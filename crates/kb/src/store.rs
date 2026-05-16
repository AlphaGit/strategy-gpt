//! SQLite-backed storage layer.
//!
//! Owns the connection and schema. See `lib.rs` for the swap-point traits.

use std::path::Path;

use rusqlite::{params, Connection, OpenFlags};

use crate::error::KbError;

const SCHEMA: &str = include_str!("schema.sql");

pub struct Store {
    pub(crate) conn: Connection,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self, KbError> {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
        )?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", true)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Store { conn })
    }

    pub fn open_in_memory() -> Result<Self, KbError> {
        let conn = Connection::open_in_memory()?;
        conn.pragma_update(None, "foreign_keys", true)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Store { conn })
    }

    pub fn source_count(&self) -> Result<i64, KbError> {
        let n: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM sources", [], |r| r.get(0))?;
        Ok(n)
    }

    pub fn chunk_count(&self) -> Result<i64, KbError> {
        let n: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM chunks", [], |r| r.get(0))?;
        Ok(n)
    }

    /// Remove all chunks, embeddings, nodes (non-Source), and edges for a
    /// given source id. The Source node itself is preserved so its identity
    /// remains stable across reingestion.
    pub fn purge_source(&mut self, source_id: &str) -> Result<(), KbError> {
        let tx = self.conn.transaction()?;
        tx.execute(
            "DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id = ?)",
            params![source_id],
        )?;
        tx.execute("DELETE FROM chunks WHERE source_id = ?", params![source_id])?;
        tx.execute(
            "DELETE FROM edges WHERE src_id IN (SELECT id FROM nodes WHERE source_id = ?) \
             OR dst_id IN (SELECT id FROM nodes WHERE source_id = ?)",
            params![source_id, source_id],
        )?;
        tx.execute(
            "DELETE FROM nodes WHERE source_id = ? AND kind != 'Source'",
            params![source_id],
        )?;
        tx.commit()?;
        Ok(())
    }
}
