//! Multi-provider market data fetching, year-segmented cache, normalization,
//! and consolidation. See spec `data-gateway`.
//!
//! The yfinance provider is feature-gated (`yfinance`) so CSV-only builds
//! avoid the HTTP/TLS dependency surface. Calendar alignment (5.6) and the
//! parquet upgrade of cache blobs (5.4) remain follow-ups.

pub mod bar;
pub mod cache;
pub mod consolidator;
pub mod divergence;
pub mod error;
pub mod gateway;
pub mod manifest;
pub mod normalizer;
pub mod provider;
pub mod providers;

pub use bar::{AdjustmentPolicy, BarRequest};
pub use cache::{BlobStore, CacheMode};
pub use consolidator::{
    ConsolidationError, ConsolidationOutcome, Consolidator, ConsolidatorConfig, DivergencePolicy,
};
pub use divergence::{DivergenceReason, DivergenceRecord, DivergenceSeverity};
pub use error::DataGatewayError;
pub use gateway::{DataGateway, DatasetResponse};
pub use manifest::{BlobKey, BlobMetadata, ManifestStore};
pub use normalizer::normalize_bars;
pub use provider::Provider;
