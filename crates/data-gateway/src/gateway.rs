//! Orchestrator: routes fetch requests through cache → provider → normalizer
//! → consolidator → caller. Issues a [`DatasetResponse`] whose `manifest_hash`
//! uniquely identifies the cache blobs used to assemble the dataset.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::Utc;
use engine_rt::Bar;
use serde::{Deserialize, Serialize};

use crate::bar::BarRequest;
use crate::cache::{BlobStore, CacheMode};
use crate::consolidator::{Consolidator, ConsolidatorConfig};
use crate::error::DataGatewayError;
use crate::manifest::{BlobKey, BlobMetadata, ManifestStore};
use crate::normalizer::normalize_bars;
use crate::provider::{Provider, ProviderQuery};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DatasetResponse {
    /// Normalized bars clipped to the requested range.
    pub bars: Vec<Bar>,
    /// Ordered list of cache blob hashes used to assemble the dataset.
    /// Replaying the same hashes against the cache returns byte-identical bars.
    pub manifest: Vec<String>,
    /// `blake3` over the concatenation of `manifest` entries — a single hash
    /// identifying the dataset.
    pub manifest_hash: String,
}

pub struct DataGateway {
    root: PathBuf,
    providers: HashMap<String, Arc<dyn Provider>>,
    blobs: BlobStore,
    manifest: ManifestStore,
    consolidator: Consolidator,
}

impl std::fmt::Debug for DataGateway {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DataGateway")
            .field("root", &self.root)
            .field("providers", &self.providers.keys().collect::<Vec<_>>())
            .finish()
    }
}

impl DataGateway {
    pub fn open(root: impl AsRef<Path>) -> Result<Self, DataGatewayError> {
        let root = root.as_ref().to_path_buf();
        std::fs::create_dir_all(&root)?;
        let blobs = BlobStore::new(root.join("blobs"))?;
        let manifest = ManifestStore::open(root.join("manifest.sqlite"))?;
        Ok(Self {
            root,
            providers: HashMap::new(),
            blobs,
            manifest,
            consolidator: Consolidator::new(ConsolidatorConfig::default()),
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn register_provider(&mut self, provider: Arc<dyn Provider>) {
        self.providers.insert(provider.name().to_string(), provider);
    }

    pub fn set_consolidator(&mut self, config: ConsolidatorConfig) {
        self.consolidator = Consolidator::new(config);
    }

    pub fn manifest(&self) -> &ManifestStore {
        &self.manifest
    }

    pub fn blobs(&self) -> &BlobStore {
        &self.blobs
    }

    /// Fetch a dataset for `request` honoring `mode`. The result is a
    /// time-sorted, range-clipped, normalized [`Vec<Bar>`] plus the manifest
    /// of cache blobs used.
    pub fn fetch(
        &self,
        request: &BarRequest,
        mode: CacheMode,
    ) -> Result<DatasetResponse, DataGatewayError> {
        if request.start >= request.end {
            return Err(DataGatewayError::InvalidRange {
                start: request.start.to_rfc3339(),
                end: request.end.to_rfc3339(),
            });
        }
        let provider = self
            .providers
            .get(&request.provider)
            .ok_or_else(|| DataGatewayError::UnknownProvider(request.provider.clone()))?
            .clone();

        let mut provider_bars: Vec<Bar> = Vec::new();
        let mut blob_hashes: Vec<String> = Vec::new();
        for year in request.years_in_range() {
            let key = BlobKey::from_inputs(
                provider.name(),
                &request.symbol,
                request.resolution,
                year,
                request.adjustment,
            );

            let bars = match mode {
                CacheMode::ForceRefresh => self.refresh_year(&provider, request, year, key)?,
                CacheMode::Offline => match self.blobs.read(key)? {
                    Some(b) => b,
                    None => {
                        return Err(DataGatewayError::OfflineMiss {
                            provider: provider.name().into(),
                            symbol: request.symbol.clone(),
                            year,
                        })
                    }
                },
                CacheMode::PreferCache | CacheMode::Validate => match self.blobs.read(key)? {
                    Some(b) => b,
                    None => self.refresh_year(&provider, request, year, key)?,
                },
            };
            blob_hashes.push(key.as_hex());
            provider_bars.extend(bars);
        }

        let combined = self
            .consolidator
            .merge(vec![(provider.name().to_string(), provider_bars)]);
        let bars = normalize_bars(combined, request.start, request.end)?;

        let manifest_hash = compute_manifest_hash(&blob_hashes);
        Ok(DatasetResponse {
            bars,
            manifest: blob_hashes,
            manifest_hash,
        })
    }

    fn refresh_year(
        &self,
        provider: &Arc<dyn Provider>,
        request: &BarRequest,
        year: i32,
        key: BlobKey,
    ) -> Result<Vec<Bar>, DataGatewayError> {
        let query = ProviderQuery {
            symbol: request.symbol.clone(),
            year,
            resolution: request.resolution,
            adjustment: request.adjustment,
        };
        let bars = provider.fetch_year(&query)?;
        let byte_size = self.blobs.write(key, &bars)?;
        self.manifest.record(&BlobMetadata {
            hash: key,
            provider: provider.name().to_string(),
            symbol: request.symbol.clone(),
            resolution: request.resolution,
            year,
            adjustment: request.adjustment,
            bar_count: bars.len() as u32,
            byte_size,
            fetched_at: Utc::now(),
        })?;
        Ok(bars)
    }
}

fn compute_manifest_hash(entries: &[String]) -> String {
    let mut hasher = blake3::Hasher::new();
    for e in entries {
        hasher.update(e.as_bytes());
        hasher.update(b"\n");
    }
    hasher.finalize().to_hex().to_string()
}
