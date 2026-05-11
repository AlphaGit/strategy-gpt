//! Cross-provider divergence records.
//!
//! Emitted by the [`crate::Consolidator`] when bars from multiple providers
//! disagree on a `(symbol, ts)`. Callers route these to the experiment
//! ledger; the data gateway does not depend on `ledger` directly so the API
//! stays one-way.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DivergenceSeverity {
    Info,
    Warn,
    Error,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DivergenceReason {
    /// Close prices differed by more than `close_tolerance_pct`.
    CloseMismatch,
    /// Volumes differed by more than `volume_tolerance_pct`.
    VolumeMismatch,
    /// One or more providers were missing a bar present elsewhere.
    BarMissing,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DivergenceRecord {
    pub symbol: String,
    pub ts: DateTime<Utc>,
    pub providers: Vec<String>,
    /// JSON object: `{ provider_name: { close, volume, ... } }`. Null for a
    /// provider missing the bar.
    pub values: Value,
    pub reason: DivergenceReason,
    pub severity: DivergenceSeverity,
}
