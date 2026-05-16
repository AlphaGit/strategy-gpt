//! Blob storage. Writes JSON bar arrays under `<root>/blobs/<hex>.json`.
//!
//! [`BlobStore`]'s public API is shape-stable, so a parquet upgrade is an
//! internal swap.

use std::path::{Path, PathBuf};

use engine_rt::Bar;

use crate::error::DataGatewayError;
use crate::manifest::BlobKey;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Default)]
pub enum CacheMode {
    /// Read from cache when available; fetch on miss. The default.
    #[default]
    PreferCache,
    /// Reserved: refresh from provider periodically to compare against
    /// cache. Currently aliased to `PreferCache`; the full refresh/diff
    /// path is a planned follow-up.
    Validate,
    /// Bypass cache for this call.
    ForceRefresh,
    /// Fail on cache miss; do not call providers.
    Offline,
}

pub struct BlobStore {
    root: PathBuf,
}

impl std::fmt::Debug for BlobStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BlobStore")
            .field("root", &self.root)
            .finish()
    }
}

impl BlobStore {
    pub fn new(root: impl AsRef<Path>) -> Result<Self, DataGatewayError> {
        let root = root.as_ref().to_path_buf();
        std::fs::create_dir_all(&root)?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn path_for(&self, key: BlobKey) -> PathBuf {
        self.root.join(format!("{}.json", key.as_hex()))
    }

    pub fn read(&self, key: BlobKey) -> Result<Option<Vec<Bar>>, DataGatewayError> {
        let path = self.path_for(key);
        if !path.exists() {
            return Ok(None);
        }
        let bytes = std::fs::read(&path)?;
        let bars: Vec<Bar> = serde_json::from_slice(&bytes)?;
        Ok(Some(bars))
    }

    pub fn write(&self, key: BlobKey, bars: &[Bar]) -> Result<u64, DataGatewayError> {
        let path = self.path_for(key);
        let bytes = serde_json::to_vec(bars)?;
        std::fs::write(&path, &bytes)?;
        Ok(bytes.len() as u64)
    }

    /// Read by the hex form returned from [`BlobKey::as_hex`]. Used by the
    /// replay path, which gets blob ids back from the ledger as strings.
    pub fn read_by_hex(&self, hex: &str) -> Result<Option<Vec<Bar>>, DataGatewayError> {
        let key = BlobKey::from_hex(hex)?;
        self.read(key)
    }
}
