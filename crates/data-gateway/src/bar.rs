//! Request types and adjustment policy.
//!
//! [`engine_rt::Bar`] is the canonical bar type returned to callers; this
//! module adds the per-request request envelope and the per-instrument
//! adjustment policy tag.

use chrono::{DateTime, Utc};
use engine_rt::Resolution;
use serde::{Deserialize, Serialize};

/// Whether bar prices have been adjusted for splits/dividends.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AdjustmentPolicy {
    /// Raw exchange prints, no corporate-action adjustments.
    Raw,
    /// Back-adjusted for splits and dividends (default for daily research).
    BackAdjusted,
}

impl AdjustmentPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            AdjustmentPolicy::Raw => "raw",
            AdjustmentPolicy::BackAdjusted => "back_adjusted",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BarRequest {
    pub provider: String,
    pub symbol: String,
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub resolution: Resolution,
    pub adjustment: AdjustmentPolicy,
    /// Optional cross-check providers. When non-empty, the gateway fetches
    /// from each, runs the consolidator across the per-provider streams, and
    /// emits `DivergenceRecord`s for any disagreement. Empty = single-provider
    /// passthrough (the default).
    #[serde(default)]
    pub secondary_providers: Vec<String>,
}

impl BarRequest {
    pub fn years_in_range(&self) -> Vec<i32> {
        use chrono::Datelike;
        let mut years = Vec::new();
        let start_year = self.start.year();
        let end_year_inclusive = if self.end.year() == self.start.year()
            || (self.end.month() == 1
                && self.end.day() == 1
                && self.end.naive_utc().time() == chrono::NaiveTime::MIN)
        {
            self.end.year()
        } else {
            self.end.year() + 1
        };
        for y in start_year..end_year_inclusive {
            years.push(y);
        }
        if years.is_empty() {
            years.push(start_year);
        }
        years
    }
}
