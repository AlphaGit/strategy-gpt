//! Backtest result types.
//!
//! The result is a research artifact, not a trade-blotter equivalent; it
//! describes one simulated run for downstream reasoning.

use chrono::{DateTime, Utc};
use engine_rt::{DecisionEvent, RunnerVersion, Side, SignalEvent};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub entry_ts: DateTime<Utc>,
    pub exit_ts: DateTime<Utc>,
    pub symbol: String,
    pub side: Side,
    pub size: f64,
    pub entry_price: f64,
    pub exit_price: f64,
    pub pnl: f64,
    pub fees: f64,
    pub reason_in: Option<String>,
    pub reason_out: Option<String>,
    /// Active signal names at the moment of entry. Snapshot taken from the
    /// signal log when the entry fill is recorded.
    pub signals_at_entry: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EquityPoint {
    pub ts: DateTime<Utc>,
    pub equity: f64,
    pub drawdown: f64,
    pub exposure: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BacktestMetrics {
    pub sharpe: f64,
    pub sortino: f64,
    pub profit_factor: f64,
    pub win_ratio: f64,
    pub max_drawdown: f64,
    pub annualized_return: f64,
    pub n_trades: u32,
    pub avg_trade_length_bars: f64,
}

impl BacktestMetrics {
    pub fn empty() -> Self {
        Self {
            sharpe: 0.0,
            sortino: 0.0,
            profit_factor: 0.0,
            win_ratio: 0.0,
            max_drawdown: 0.0,
            annualized_return: 0.0,
            n_trades: 0,
            avg_trade_length_bars: 0.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ResultMeta {
    pub strategy_artifact: String,
    pub dataset_manifest: String,
    pub seed: u64,
    pub runner_version: RunnerVersion,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BacktestResult {
    pub meta: ResultMeta,
    pub metrics: BacktestMetrics,
    pub trades: Vec<Trade>,
    pub signals: Vec<SignalEvent>,
    pub equity: Vec<EquityPoint>,
    pub exec_log: Vec<DecisionEvent>,
    /// Post-hoc regime tags. Land in 4.10; empty for now.
    #[serde(default)]
    pub regimes: Vec<RegimeTag>,
    /// Stress sub-results. Land in 4.7/4.8; absent for now.
    #[serde(default)]
    pub stress: Option<StressResult>,
    /// Sensitivity sub-results. Land in 4.9; absent for now.
    #[serde(default)]
    pub sensitivity: Option<SensitivityResult>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RegimeTag {
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub label: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StressResult {
    pub scenarios: Vec<StressScenario>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StressScenario {
    pub name: String,
    pub perturbation: serde_json::Value,
    pub metrics: BacktestMetrics,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SensitivityResult {
    pub param: String,
    pub points: Vec<SensitivityPoint>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SensitivityPoint {
    pub value: f64,
    pub metrics: BacktestMetrics,
}
