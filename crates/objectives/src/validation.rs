//! Spec self-consistency validation.

use std::collections::HashSet;

use thiserror::Error;

use crate::metric_name::is_valid_metric;
use crate::spec::{ObjectiveSpec, Tradeoff};

#[derive(Debug, Error)]
pub enum ValidationError {
    #[error("unknown metric `{0}`; valid: {1}")]
    UnknownMetric(String, String),

    #[error("metric `{0}` declared more than once across primary + secondary")]
    DuplicateMetric(String),

    #[error("weight on `{0}` is negative ({1})")]
    NegativeWeight(String, f64),

    #[error("pareto tradeoff requires at least two contributing metrics; got {0}")]
    ParetoNeedsTwoMetrics(usize),

    #[error("walk_forward.folds must be >= 1; got {0}")]
    InvalidFolds(u32),

    #[error("walk_forward.gap ({gap}) is larger than folds ({folds})")]
    GapLargerThanFolds { gap: u32, folds: u32 },
}

pub fn validate(spec: &ObjectiveSpec) -> Result<(), ValidationError> {
    // Metric existence.
    if !is_valid_metric(&spec.primary.metric) {
        return Err(ValidationError::UnknownMetric(
            spec.primary.metric.clone(),
            valid_list(),
        ));
    }
    for s in &spec.secondary {
        if !is_valid_metric(&s.metric) {
            return Err(ValidationError::UnknownMetric(
                s.metric.clone(),
                valid_list(),
            ));
        }
    }

    // No metric repeats across primary + secondary.
    let mut seen: HashSet<&str> = HashSet::new();
    if !seen.insert(spec.primary.metric.as_str()) {
        return Err(ValidationError::DuplicateMetric(
            spec.primary.metric.clone(),
        ));
    }
    for s in &spec.secondary {
        if !seen.insert(s.metric.as_str()) {
            return Err(ValidationError::DuplicateMetric(s.metric.clone()));
        }
    }

    // Weights non-negative.
    if spec.primary.weight < 0.0 {
        return Err(ValidationError::NegativeWeight(
            spec.primary.metric.clone(),
            spec.primary.weight,
        ));
    }
    for s in &spec.secondary {
        if s.weight < 0.0 {
            return Err(ValidationError::NegativeWeight(s.metric.clone(), s.weight));
        }
    }

    // Pareto requires >= 2 metrics.
    if matches!(spec.tradeoff, Tradeoff::Pareto) {
        let count = 1 + spec.secondary.len();
        if count < 2 {
            return Err(ValidationError::ParetoNeedsTwoMetrics(count));
        }
    }

    // Walk-forward sanity.
    if spec.walk_forward.folds == 0 {
        return Err(ValidationError::InvalidFolds(spec.walk_forward.folds));
    }
    if let Some(gap) = spec.walk_forward.gap {
        if gap >= spec.walk_forward.folds {
            return Err(ValidationError::GapLargerThanFolds {
                gap,
                folds: spec.walk_forward.folds,
            });
        }
    }

    Ok(())
}

fn valid_list() -> String {
    crate::metric_name::ENGINE_METRICS.join(", ")
}
