//! Multi-provider market data fetching, year-segmented cache, normalization,
//! and consolidation. See spec `data-gateway`.
//!
//! v1 scope:
//! - [`Provider`] trait + [`providers::CsvProvider`].
//! - Year-segmented content-addressed cache in JSON blobs under `<root>/blobs/`.
//! - SQLite manifest at `<root>/manifest.sqlite` listing every cached blob.
//! - [`CacheMode`]: `PreferCache`, `Validate` (stub), `ForceRefresh`, `Offline`.
//! - Internal-only [`Consolidator`] policy with single-provider passthrough.
//! - [`Manifest`] handles issued with every dataset return.
//!
//! Out of v1 (tracked in tasks 5.2, 5.6, 5.7, 5.8, 5.10): yfinance provider,
//! exchange-calendar alignment, multi-provider divergence detection +
//! warnings-to-ledger, PyO3 bindings.

pub mod bar;
pub mod cache;
pub mod consolidator;
pub mod error;
pub mod gateway;
pub mod manifest;
pub mod normalizer;
pub mod provider;
pub mod providers;

pub use bar::{AdjustmentPolicy, BarRequest};
pub use cache::{BlobStore, CacheMode};
pub use consolidator::{Consolidator, ConsolidatorConfig};
pub use error::DataGatewayError;
pub use gateway::{DataGateway, DatasetResponse};
pub use manifest::{BlobKey, BlobMetadata, ManifestStore};
pub use normalizer::normalize_bars;
pub use provider::Provider;
