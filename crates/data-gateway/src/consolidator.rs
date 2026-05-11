//! Multi-provider bar consolidation.
//!
//! v1 ships single-provider passthrough plus internal-only structure for the
//! richer policy (precedence order, close/volume tolerance, on-disagree
//! behavior, missing-bar handling). Real consolidation across multiple
//! providers lands with task 5.7 + divergence-warning routing in 5.8.

use engine_rt::Bar;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DivergencePolicy {
    /// Use the higher-precedence provider's value, log a warning.
    PickPrecedence,
    /// Reject the request when divergence exceeds tolerance.
    Fail,
    /// Use the per-bar median across providers.
    Median,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ConsolidatorConfig {
    /// Provider names in descending precedence order. The first provider in
    /// this list whose bar is present is the canonical source unless a
    /// median/disagree policy says otherwise.
    pub precedence: Vec<String>,
    pub close_tolerance_pct: f64,
    pub volume_tolerance_pct: f64,
    pub on_disagree: DivergencePolicy,
}

impl Default for ConsolidatorConfig {
    fn default() -> Self {
        Self {
            precedence: vec![],
            close_tolerance_pct: 0.001, // 10 bps
            volume_tolerance_pct: 0.05, // 5% per spec discussion (info-level)
            on_disagree: DivergencePolicy::PickPrecedence,
        }
    }
}

/// Stateless merger: deterministic given the same input order.
pub struct Consolidator {
    config: ConsolidatorConfig,
}

impl Consolidator {
    pub fn new(config: ConsolidatorConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &ConsolidatorConfig {
        &self.config
    }

    /// V1 single-provider passthrough. Real multi-provider merging lives
    /// behind the same signature so callers do not change when the swap lands.
    pub fn merge(&self, mut per_provider: Vec<(String, Vec<Bar>)>) -> Vec<Bar> {
        if per_provider.is_empty() {
            return Vec::new();
        }
        if per_provider.len() == 1 {
            return per_provider.remove(0).1;
        }
        // Multi-provider merge stub: pick the highest-precedence provider.
        let preferred = self
            .config
            .precedence
            .iter()
            .find_map(|name| per_provider.iter().position(|(p, _)| p == name))
            .unwrap_or(0);
        per_provider.swap_remove(preferred).1
    }
}
