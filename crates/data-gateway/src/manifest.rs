//! SQLite manifest of cached blobs.
//!
//! Schema:
//! ```sql
//! CREATE TABLE blobs (
//!     hash TEXT PRIMARY KEY,
//!     provider TEXT NOT NULL,
//!     symbol TEXT NOT NULL,
//!     resolution TEXT NOT NULL,
//!     year INTEGER NOT NULL,
//!     adjustment TEXT NOT NULL,
//!     bar_count INTEGER NOT NULL,
//!     byte_size INTEGER NOT NULL,
//!     fetched_at TEXT NOT NULL,
//!     UNIQUE (provider, symbol, resolution, year, adjustment)
//! );
//! ```

use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use engine_rt::Resolution;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

use crate::bar::AdjustmentPolicy;
use crate::error::DataGatewayError;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct BlobKey([u8; 32]);

impl BlobKey {
    pub fn from_inputs(
        provider: &str,
        symbol: &str,
        resolution: Resolution,
        year: i32,
        adjustment: AdjustmentPolicy,
    ) -> Self {
        let mut hasher = blake3::Hasher::new();
        hasher.update(provider.as_bytes());
        hasher.update(b"\n");
        hasher.update(symbol.as_bytes());
        hasher.update(b"\n");
        hasher.update(resolution_str(resolution).as_bytes());
        hasher.update(b"\n");
        hasher.update(year.to_string().as_bytes());
        hasher.update(b"\n");
        hasher.update(adjustment.as_str().as_bytes());
        Self(*hasher.finalize().as_bytes())
    }

    pub fn as_hex(&self) -> String {
        let mut s = String::with_capacity(64);
        for b in self.0 {
            s.push_str(&format!("{b:02x}"));
        }
        s
    }

    /// Inverse of [`Self::as_hex`]. Required by the replay path so a
    /// recorded manifest hash list can be turned back into cache keys.
    pub fn from_hex(hex: &str) -> Result<Self, DataGatewayError> {
        if hex.len() != 64 {
            return Err(DataGatewayError::Internal(format!(
                "blob hex must be 64 chars, got {}",
                hex.len()
            )));
        }
        let mut out = [0u8; 32];
        for (i, byte) in out.iter_mut().enumerate() {
            let s = &hex[i * 2..i * 2 + 2];
            *byte = u8::from_str_radix(s, 16).map_err(|e| {
                DataGatewayError::Internal(format!("blob hex `{hex}` malformed at byte {i}: {e}"))
            })?;
        }
        Ok(Self(out))
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BlobMetadata {
    pub hash: BlobKey,
    pub provider: String,
    pub symbol: String,
    pub resolution: Resolution,
    pub year: i32,
    pub adjustment: AdjustmentPolicy,
    pub bar_count: u32,
    pub byte_size: u64,
    pub fetched_at: DateTime<Utc>,
}

pub struct ManifestStore {
    conn: Connection,
    db_path: PathBuf,
}

impl std::fmt::Debug for ManifestStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ManifestStore")
            .field("db_path", &self.db_path)
            .finish()
    }
}

impl ManifestStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, DataGatewayError> {
        let db_path = path.as_ref().to_path_buf();
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(&db_path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS blobs (
                hash       TEXT PRIMARY KEY,
                provider   TEXT NOT NULL,
                symbol     TEXT NOT NULL,
                resolution TEXT NOT NULL,
                year       INTEGER NOT NULL,
                adjustment TEXT NOT NULL,
                bar_count  INTEGER NOT NULL,
                byte_size  INTEGER NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE (provider, symbol, resolution, year, adjustment)
            );
            CREATE INDEX IF NOT EXISTS idx_blobs_lookup
                ON blobs (provider, symbol, resolution, year, adjustment);
            "#,
        )?;
        Ok(Self { conn, db_path })
    }

    pub fn lookup(&self, key: BlobKey) -> Result<Option<BlobMetadata>, DataGatewayError> {
        let row = self
            .conn
            .query_row(
                "SELECT hash, provider, symbol, resolution, year, adjustment,
                        bar_count, byte_size, fetched_at
                 FROM blobs WHERE hash = ?1",
                params![key.as_hex()],
                |row| {
                    Ok(RawBlobRow {
                        _hash: row.get::<_, String>(0)?,
                        provider: row.get(1)?,
                        symbol: row.get(2)?,
                        resolution_str: row.get(3)?,
                        year: row.get(4)?,
                        adjustment_str: row.get(5)?,
                        bar_count: row.get(6)?,
                        byte_size: row.get(7)?,
                        fetched_at_str: row.get(8)?,
                    })
                },
            )
            .optional()?;
        let Some(r) = row else { return Ok(None) };
        let resolution = parse_resolution(&r.resolution_str)?;
        let adjustment = parse_adjustment(&r.adjustment_str)?;
        let fetched_at = DateTime::parse_from_rfc3339(&r.fetched_at_str)
            .map(|t| t.with_timezone(&Utc))
            .map_err(|e| DataGatewayError::Internal(format!("invalid fetched_at rfc3339: {e}")))?;
        Ok(Some(BlobMetadata {
            hash: key,
            provider: r.provider,
            symbol: r.symbol,
            resolution,
            year: r.year as i32,
            adjustment,
            bar_count: r.bar_count as u32,
            byte_size: r.byte_size as u64,
            fetched_at,
        }))
    }

    pub fn record(&self, meta: &BlobMetadata) -> Result<(), DataGatewayError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO blobs
                (hash, provider, symbol, resolution, year, adjustment,
                 bar_count, byte_size, fetched_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![
                meta.hash.as_hex(),
                meta.provider,
                meta.symbol,
                resolution_str(meta.resolution),
                meta.year as i64,
                meta.adjustment.as_str(),
                meta.bar_count as i64,
                meta.byte_size as i64,
                meta.fetched_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn db_path(&self) -> &Path {
        &self.db_path
    }
}

struct RawBlobRow {
    _hash: String,
    provider: String,
    symbol: String,
    resolution_str: String,
    year: i64,
    adjustment_str: String,
    bar_count: i64,
    byte_size: i64,
    fetched_at_str: String,
}

pub fn resolution_str(r: Resolution) -> &'static str {
    match r {
        Resolution::Minute => "1m",
        Resolution::FiveMinute => "5m",
        Resolution::FifteenMinute => "15m",
        Resolution::Hour => "1h",
        Resolution::Day => "1d",
        Resolution::Week => "1w",
    }
}

fn parse_resolution(s: &str) -> Result<Resolution, DataGatewayError> {
    Ok(match s {
        "1m" => Resolution::Minute,
        "5m" => Resolution::FiveMinute,
        "15m" => Resolution::FifteenMinute,
        "1h" => Resolution::Hour,
        "1d" => Resolution::Day,
        "1w" => Resolution::Week,
        other => {
            return Err(DataGatewayError::Internal(format!(
                "unknown resolution string `{other}`"
            )))
        }
    })
}

fn parse_adjustment(s: &str) -> Result<AdjustmentPolicy, DataGatewayError> {
    Ok(match s {
        "raw" => AdjustmentPolicy::Raw,
        "back_adjusted" => AdjustmentPolicy::BackAdjusted,
        other => {
            return Err(DataGatewayError::Internal(format!(
                "unknown adjustment string `{other}`"
            )))
        }
    })
}
