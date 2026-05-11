//! Ledger record types. One per table.

use chrono::{DateTime, Utc};
use engine::spec::{EngineConfig, TimeRange};
use engine_rt::RunnerVersion;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RunRecord {
    pub id: String,
    pub strategy_artifact: String,
    pub dataset_manifest_hash: String,
    pub hypothesis_id: Option<String>,
    pub parameters: Value,
    pub modes: Value,
    pub seed: u64,
    pub runner_version: RunnerVersion,
    /// Half-open time slice the run was executed over. Required for byte-
    /// identical replay (`spec::reproducibility-from-ledger-alone`).
    pub slice: TimeRange,
    /// Engine configuration (fill model, capital, fees, slippage, sanity
    /// bounds). Required for byte-identical replay.
    pub engine_config: EngineConfig,
    /// Parallelism the batch was issued with. Recorded for completeness;
    /// replay uses `1` regardless because per-run determinism does not
    /// depend on worker count.
    pub parallelism: usize,
    pub verdict: Option<Value>,
    pub metrics: Option<Value>,
    /// Path to the directory holding this run's sidecars (trades.json,
    /// signals.json, equity.json, exec_log.json), relative to the ledger
    /// root. None if no sidecars were stored.
    pub sidecar_root: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HypothesisRecord {
    pub id: String,
    pub name: String,
    pub target_metric: String,
    pub falsification: Value,
    pub proposed_change: Value,
    pub kb_cites: Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionKind {
    Accepted,
    Rejected,
}

impl DecisionKind {
    pub fn as_str(self) -> &'static str {
        match self {
            DecisionKind::Accepted => "accepted",
            DecisionKind::Rejected => "rejected",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "accepted" => Some(DecisionKind::Accepted),
            "rejected" => Some(DecisionKind::Rejected),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionRecord {
    pub id: String,
    pub hypothesis_id: String,
    pub kind: DecisionKind,
    pub rationale: String,
    pub evidence: Value,
    pub decided_at: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DatasetManifestRecord {
    pub hash: String,
    pub manifest: Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DivergenceSeverity {
    Info,
    Warn,
    Error,
}

impl DivergenceSeverity {
    pub fn as_str(self) -> &'static str {
        match self {
            DivergenceSeverity::Info => "info",
            DivergenceSeverity::Warn => "warn",
            DivergenceSeverity::Error => "error",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "info" => Some(DivergenceSeverity::Info),
            "warn" => Some(DivergenceSeverity::Warn),
            "error" => Some(DivergenceSeverity::Error),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DivergenceWarning {
    pub symbol: String,
    pub ts: DateTime<Utc>,
    pub providers: Vec<String>,
    pub values: Value,
    pub reason: String,
    pub severity: DivergenceSeverity,
    pub logged_at: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ObjectiveRecord {
    pub strategy_id: String,
    pub spec: Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StrategyVersionRecord {
    pub artifact_hash: String,
    pub runner_version: RunnerVersion,
    pub metadata: Value,
    pub created_at: DateTime<Utc>,
}
