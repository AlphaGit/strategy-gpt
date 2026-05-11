//! Multi-provider bar consolidation.
//!
//! [`Consolidator::merge`] aligns per-provider bars by `(symbol, ts)`, applies
//! the configured tolerance and on-disagree policy, and returns merged bars
//! plus [`DivergenceRecord`]s for any disagreement the caller should record.

use std::collections::{BTreeMap, HashSet};

use chrono::{DateTime, Utc};
use engine_rt::Bar;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::divergence::{DivergenceReason, DivergenceRecord, DivergenceSeverity};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DivergencePolicy {
    /// Use the higher-precedence provider's value, log a warning.
    PickPrecedence,
    /// Reject the request when divergence exceeds tolerance.
    Fail,
    /// Use the per-bar median close across providers.
    Median,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ConsolidatorConfig {
    /// Provider names in descending precedence order.
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
            volume_tolerance_pct: 0.05, // 5%
            on_disagree: DivergencePolicy::PickPrecedence,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ConsolidationOutcome {
    pub bars: Vec<Bar>,
    pub warnings: Vec<DivergenceRecord>,
}

#[derive(Debug, thiserror::Error)]
pub enum ConsolidationError {
    #[error("divergence policy `Fail` triggered: {0:?}")]
    FailPolicyTriggered(DivergenceRecord),
}

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

    /// Merge per-provider bar streams. Each entry is `(provider_name, bars)`.
    /// Bars within a provider must already be sorted by `ts` (the normalizer
    /// guarantees this).
    pub fn merge(
        &self,
        per_provider: Vec<(String, Vec<Bar>)>,
    ) -> Result<ConsolidationOutcome, ConsolidationError> {
        if per_provider.is_empty() {
            return Ok(ConsolidationOutcome {
                bars: Vec::new(),
                warnings: Vec::new(),
            });
        }
        if per_provider.len() == 1 {
            let mut pp = per_provider;
            return Ok(ConsolidationOutcome {
                bars: pp.remove(0).1,
                warnings: Vec::new(),
            });
        }

        // Index every provider's bars by ts.
        // BTreeMap<DateTime, Vec<(provider, bar)>>
        let mut by_ts: BTreeMap<DateTime<Utc>, Vec<(String, Bar)>> = BTreeMap::new();
        let all_providers: Vec<String> = per_provider.iter().map(|(p, _)| p.clone()).collect();
        for (provider, bars) in per_provider {
            for bar in bars {
                by_ts
                    .entry(bar.ts)
                    .or_default()
                    .push((provider.clone(), bar));
            }
        }

        let mut out_bars = Vec::new();
        let mut warnings = Vec::new();
        let all_set: HashSet<&str> = all_providers.iter().map(String::as_str).collect();

        for (ts, mut providers_here) in by_ts {
            // Symbol is taken from the first present bar.
            let symbol = providers_here[0].1.symbol.clone();

            // Missing providers for this ts.
            let present_set: HashSet<&str> =
                providers_here.iter().map(|(p, _)| p.as_str()).collect();
            let missing: Vec<&str> = all_set.difference(&present_set).copied().collect();
            if !missing.is_empty() {
                let mut values = serde_json::Map::new();
                for (p, b) in &providers_here {
                    values.insert(p.clone(), json!({ "close": b.close, "volume": b.volume }));
                }
                for m in &missing {
                    values.insert((*m).to_string(), serde_json::Value::Null);
                }
                warnings.push(DivergenceRecord {
                    symbol: symbol.clone(),
                    ts,
                    providers: all_providers.clone(),
                    values: serde_json::Value::Object(values),
                    reason: DivergenceReason::BarMissing,
                    severity: DivergenceSeverity::Info,
                });
            }

            // Compare close across providers present.
            if providers_here.len() > 1 {
                let close_values: Vec<f64> = providers_here.iter().map(|(_, b)| b.close).collect();
                let close_diverged = diverges_pct(&close_values, self.config.close_tolerance_pct);
                let vol_values: Vec<f64> = providers_here.iter().map(|(_, b)| b.volume).collect();
                let vol_diverged = diverges_pct(&vol_values, self.config.volume_tolerance_pct);

                if close_diverged || vol_diverged {
                    let mut values = serde_json::Map::new();
                    for (p, b) in &providers_here {
                        values.insert(p.clone(), json!({ "close": b.close, "volume": b.volume }));
                    }
                    let reason = if close_diverged {
                        DivergenceReason::CloseMismatch
                    } else {
                        DivergenceReason::VolumeMismatch
                    };
                    let severity = match (close_diverged, reason) {
                        (true, _) => DivergenceSeverity::Warn,
                        (false, DivergenceReason::VolumeMismatch) => DivergenceSeverity::Info,
                        _ => DivergenceSeverity::Info,
                    };
                    let record = DivergenceRecord {
                        symbol: symbol.clone(),
                        ts,
                        providers: all_providers.clone(),
                        values: serde_json::Value::Object(values),
                        reason,
                        severity,
                    };
                    if close_diverged && matches!(self.config.on_disagree, DivergencePolicy::Fail) {
                        return Err(ConsolidationError::FailPolicyTriggered(record));
                    }
                    warnings.push(record);
                }
            }

            // Choose the bar for this ts per policy.
            let chosen = match self.config.on_disagree {
                DivergencePolicy::Median if providers_here.len() > 1 => {
                    let mut closes: Vec<(usize, f64)> = providers_here
                        .iter()
                        .enumerate()
                        .map(|(i, (_, b))| (i, b.close))
                        .collect();
                    closes
                        .sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
                    let median_idx = closes[closes.len() / 2].0;
                    providers_here.swap_remove(median_idx).1
                }
                _ => self.pick_by_precedence(&mut providers_here),
            };
            out_bars.push(chosen);
        }

        out_bars.sort_by_key(|b| b.ts);
        Ok(ConsolidationOutcome {
            bars: out_bars,
            warnings,
        })
    }

    fn pick_by_precedence(&self, providers_here: &mut Vec<(String, Bar)>) -> Bar {
        if let Some(preferred) = self
            .config
            .precedence
            .iter()
            .find_map(|name| providers_here.iter().position(|(p, _)| p == name))
        {
            return providers_here.swap_remove(preferred).1;
        }
        providers_here.swap_remove(0).1
    }
}

fn diverges_pct(values: &[f64], tolerance_pct: f64) -> bool {
    if values.len() < 2 {
        return false;
    }
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if min <= 0.0 {
        // Avoid division-by-zero; treat any difference around zero as diverged.
        return (max - min).abs() > tolerance_pct;
    }
    (max - min) / min > tolerance_pct
}
