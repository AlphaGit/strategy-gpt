//! Provider trait: pluggable bar source.
//!
//! Providers are registered by name on the [`crate::DataGateway`] at startup.
//! Every fetch call routes through the gateway, which handles caching,
//! normalization, and (later) consolidation.

use chrono::{DateTime, Utc};
use engine_rt::{Bar, Resolution};

use crate::bar::AdjustmentPolicy;
use crate::error::DataGatewayError;

/// Lookup parameters passed to a [`Provider`] when the gateway needs to
/// refresh a year-sized slice of cache. Providers MUST return bars for the
/// full year (clipped to existence) — the gateway slices to the caller's
/// finer-grained range.
#[derive(Clone, Debug, PartialEq)]
pub struct ProviderQuery {
    pub symbol: String,
    pub year: i32,
    pub resolution: Resolution,
    pub adjustment: AdjustmentPolicy,
}

impl ProviderQuery {
    pub fn year_start(&self) -> DateTime<Utc> {
        use chrono::TimeZone;
        Utc.with_ymd_and_hms(self.year, 1, 1, 0, 0, 0).unwrap()
    }

    pub fn year_end(&self) -> DateTime<Utc> {
        use chrono::TimeZone;
        Utc.with_ymd_and_hms(self.year + 1, 1, 1, 0, 0, 0).unwrap()
    }
}

pub trait Provider: Send + Sync {
    /// Stable name used in cache keys and divergence warnings.
    fn name(&self) -> &str;

    /// Fetch a year's worth of bars. May return empty if the provider has no
    /// data for that `(symbol, year, resolution, adjustment)`.
    fn fetch_year(&self, query: &ProviderQuery) -> Result<Vec<Bar>, DataGatewayError>;
}
