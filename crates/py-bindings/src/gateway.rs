//! PyO3 bindings for `data_gateway::DataGateway`.
//!
//! Surface:
//! - `PyDataGateway(root: str)` — open a gateway rooted at `root`.
//! - `register_csv_provider(name: str, base_dir: str)` — register a CSV provider.
//! - `fetch(request_json: str, mode: str) -> str` — fetch a dataset; returns
//!   JSON-serialized `DatasetResponse` (`bars`, `manifest`, `manifest_hash`,
//!   `warnings`).
//! - `cache_stats() -> str` — JSON `{ blob_count, total_bytes }` over the
//!   on-disk blob store.
//! - `root() -> str` — filesystem root.
//!
//! `mode` accepts: `"prefer_cache"`, `"validate"`, `"force_refresh"`,
//! `"offline"` (matching `data_gateway::CacheMode` snake_case names).

use std::sync::{Arc, Mutex};

use data_gateway::{BarRequest, CacheMode, DataGateway};
use pyo3::prelude::*;
use serde::Serialize;

use crate::{io_err, json_err, runtime_err};

#[pyclass(
    module = "strategy_gpt_native.gateway",
    name = "DataGateway",
    unsendable
)]
pub struct PyDataGateway {
    inner: Arc<Mutex<DataGateway>>,
}

#[pymethods]
impl PyDataGateway {
    #[new]
    fn new(root: &str) -> PyResult<Self> {
        let g = DataGateway::open(root).map_err(io_err)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(g)),
        })
    }

    fn root(&self) -> PyResult<String> {
        let g = self.inner.lock().map_err(runtime_err)?;
        Ok(g.root().display().to_string())
    }

    fn register_csv_provider(&self, name: &str, base_dir: &str) -> PyResult<()> {
        let provider = data_gateway::providers::CsvProvider::new(name, base_dir);
        let mut g = self.inner.lock().map_err(runtime_err)?;
        g.register_provider(Arc::new(provider));
        Ok(())
    }

    fn fetch(&self, request_json: &str, mode: &str) -> PyResult<String> {
        let request: BarRequest = serde_json::from_str(request_json).map_err(json_err)?;
        let cache_mode = parse_cache_mode(mode)?;
        let g = self.inner.lock().map_err(runtime_err)?;
        let response = g.fetch(&request, cache_mode).map_err(runtime_err)?;
        serde_json::to_string(&response).map_err(runtime_err)
    }

    fn cache_stats(&self) -> PyResult<String> {
        let g = self.inner.lock().map_err(runtime_err)?;
        let stats = compute_cache_stats(g.blobs().root()).map_err(io_err)?;
        serde_json::to_string(&stats).map_err(runtime_err)
    }

    /// Reconstruct a dataset from a ledger-stored manifest. `manifest_json`
    /// is the `DatasetManifestRecord.manifest` field (the
    /// `{ "request": ..., "blobs": [...] }` shape — see
    /// [`data_gateway::DataGateway::load_dataset_from_manifest`]). Returns
    /// the same JSON-serialized [`data_gateway::DatasetResponse`] shape as
    /// `fetch`.
    fn load_dataset_from_manifest(&self, manifest_json: &str) -> PyResult<String> {
        let manifest: serde_json::Value = serde_json::from_str(manifest_json).map_err(json_err)?;
        let g = self.inner.lock().map_err(runtime_err)?;
        let response = g
            .load_dataset_from_manifest(&manifest)
            .map_err(runtime_err)?;
        serde_json::to_string(&response).map_err(runtime_err)
    }
}

/// Internal accessor for cross-pyclass replay (see `ledger_mod::replay_run`).
impl PyDataGateway {
    pub(crate) fn handle(&self) -> Arc<Mutex<DataGateway>> {
        Arc::clone(&self.inner)
    }
}

fn parse_cache_mode(s: &str) -> PyResult<CacheMode> {
    match s {
        "prefer_cache" => Ok(CacheMode::PreferCache),
        "validate" => Ok(CacheMode::Validate),
        "force_refresh" => Ok(CacheMode::ForceRefresh),
        "offline" => Ok(CacheMode::Offline),
        other => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "unknown cache mode `{other}`; expected one of \
             prefer_cache, validate, force_refresh, offline"
        ))),
    }
}

#[derive(Serialize)]
struct CacheStats {
    blob_count: u64,
    total_bytes: u64,
}

fn compute_cache_stats(root: &std::path::Path) -> std::io::Result<CacheStats> {
    let mut blob_count = 0u64;
    let mut total_bytes = 0u64;
    if !root.exists() {
        return Ok(CacheStats {
            blob_count,
            total_bytes,
        });
    }
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("json") {
            let meta = entry.metadata()?;
            blob_count += 1;
            total_bytes += meta.len();
        }
    }
    Ok(CacheStats {
        blob_count,
        total_bytes,
    })
}
