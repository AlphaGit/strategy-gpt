//! Batch specification types.
//!
//! A [`BatchSpec`] is the entire input to a backtest invocation: one
//! strategy, one dataset reference, and N run configurations. The engine
//! compiles the strategy at most once per batch and executes every run
//! across an internal worker pool. See spec `backtest-engine`.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::fill_model::FillModel;
use crate::sanity::SanityBounds;

/// Identifier for the cached strategy artifact a batch references. The build
/// pipeline produces these (`build_pipeline::ArtifactKey::as_hex()`); the
/// engine treats them as opaque strings.
#[derive(Clone, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct StrategyArtifactRef(pub String);

/// Identifier for a cached dataset. Issued by the data gateway's manifest
/// system; engine treats it as opaque.
#[derive(Clone, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct DatasetRef(pub String);

/// Half-open `[start, end)` time slice expressed in UTC.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TimeRange {
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
}

impl TimeRange {
    pub fn contains(&self, ts: DateTime<Utc>) -> bool {
        ts >= self.start && ts < self.end
    }
}

/// Strategy parameter set. The map is opaque to the engine; the strategy's
/// `on_init` consumes it via `serde_json::from_value`.
pub type ParamSet = serde_json::Value;

/// Execution mode for a run. Stress and sensitivity modes are first-class
/// rather than separate scripts; each mode carries its own parameters.
///
/// Currently only [`Mode::Plain`] is implemented in the executor; other modes
/// are reserved here so [`RunSpec`] is shape-stable.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Mode {
    Plain,
    MonteCarlo { n: u32, block_size: u32 },
    Slippage { bps_grid: Vec<f64> },
    RegimeFilter { ranges: Vec<TimeRange> },
    Sensitivity { param: String, values: Vec<f64> },
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RunSpec {
    pub params: ParamSet,
    pub modes: Vec<Mode>,
    pub seed: u64,
    pub slice: TimeRange,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EngineConfig {
    pub fill_model: FillModel,
    pub initial_capital: f64,
    /// Per-fill commission charged in the same currency as price * size.
    pub commission_per_fill: f64,
    /// Slippage applied as a fixed fraction of price (e.g. 0.0005 = 5 bps).
    pub slippage_bps: f64,
    pub sanity: SanityBounds,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            fill_model: FillModel::default(),
            initial_capital: 100_000.0,
            commission_per_fill: 0.0,
            slippage_bps: 0.0,
            sanity: SanityBounds::default(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BatchSpec {
    pub strategy: StrategyArtifactRef,
    pub dataset: DatasetRef,
    pub runs: Vec<RunSpec>,
    pub engine: EngineConfig,
    /// Soft limit on parallelism. The single-process executor used by tests
    /// ignores this; the multi-worker coordinator (task 4.3) will respect it.
    pub parallelism: usize,
}
