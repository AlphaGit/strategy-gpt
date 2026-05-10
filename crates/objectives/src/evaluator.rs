//! Apply an [`ObjectiveSpec`] to a [`BacktestMetrics`] snapshot.
//!
//! Returns an [`EvaluationOutcome`] capturing acceptance, aggregated score
//! under the configured tradeoff, and any constraint violations. Pareto mode
//! returns a scalar score equal to the primary metric value; the caller is
//! responsible for accumulating frontier candidates.

use engine::BacktestMetrics;
use serde::{Deserialize, Serialize};

use crate::metric_name::metric_value;
use crate::spec::{Comparison, ObjectiveSpec, SecondaryMode, Tradeoff};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EvaluationOutcome {
    /// True if no constraint metric was violated and OOS gate (if any) passed.
    /// Soft secondary metrics never cause rejection.
    pub accepted: bool,
    /// Aggregated score for the candidate. Higher is better.
    /// - `Lexicographic`: primary metric value (callers break ties manually).
    /// - `WeightedSum`: primary * w_p + Σ secondary_soft * w_s (negated for
    ///   metrics whose target uses `<=` or `<` so lower-is-better terms
    ///   contribute consistently).
    /// - `Pareto`: primary metric value (caller assembles the frontier).
    pub score: f64,
    /// Names of constraint metrics whose targets were not satisfied.
    pub violations: Vec<String>,
    /// Names of soft secondary metrics whose targets were not satisfied.
    pub soft_misses: Vec<String>,
}

pub fn evaluate(metrics: &BacktestMetrics, spec: &ObjectiveSpec) -> EvaluationOutcome {
    let mut violations = Vec::new();
    let mut soft_misses = Vec::new();

    // Primary target failure is treated as a soft miss (callers can decide
    // separately whether to reject). Specs that want primary as a hard gate
    // can express it via a constraint secondary referencing the same metric
    // — but our validator forbids duplicate metric names. So primary's target
    // is informational; constraint secondaries are the gating mechanism.
    if let Some(target) = &spec.primary.target {
        let v = metric_value(metrics, &spec.primary.metric).unwrap_or(0.0);
        if !target.satisfied_by(v) {
            soft_misses.push(spec.primary.metric.clone());
        }
    }

    for sec in &spec.secondary {
        let v = metric_value(metrics, &sec.metric).unwrap_or(0.0);
        let ok = sec.target.satisfied_by(v);
        if !ok {
            match sec.mode {
                SecondaryMode::Constraint => violations.push(sec.metric.clone()),
                SecondaryMode::Soft => soft_misses.push(sec.metric.clone()),
            }
        }
    }

    let accepted = violations.is_empty();

    let score = if !accepted {
        f64::NEG_INFINITY
    } else {
        match spec.tradeoff {
            Tradeoff::Lexicographic | Tradeoff::Pareto => {
                metric_value(metrics, &spec.primary.metric).unwrap_or(0.0)
            }
            Tradeoff::WeightedSum => weighted_sum_score(metrics, spec),
        }
    };

    EvaluationOutcome {
        accepted,
        score,
        violations,
        soft_misses,
    }
}

fn weighted_sum_score(metrics: &BacktestMetrics, spec: &ObjectiveSpec) -> f64 {
    let primary = metric_value(metrics, &spec.primary.metric).unwrap_or(0.0);
    let primary_sign = sign_for_target(spec.primary.target.as_ref());
    let mut sum = primary * spec.primary.weight * primary_sign;
    for sec in &spec.secondary {
        if !matches!(sec.mode, SecondaryMode::Soft) {
            continue;
        }
        let v = metric_value(metrics, &sec.metric).unwrap_or(0.0);
        let sign = sign_for_target(Some(&sec.target));
        sum += v * sec.weight * sign;
    }
    sum
}

/// `+1` when target is `>=` / `>` / `==` / `None` (higher-is-better default);
/// `-1` when target is `<=` / `<` (lower-is-better).
fn sign_for_target(target: Option<&Comparison>) -> f64 {
    use crate::spec::ComparisonOp;
    match target.map(|t| t.op) {
        Some(ComparisonOp::Le) | Some(ComparisonOp::Lt) => -1.0,
        _ => 1.0,
    }
}
