//! Per-strategy declarative objective specifications.
//!
//! Evaluator and Parameter Optimizer both read the same [`ObjectiveSpec`]
//! and apply identical rules for metric targets, weights, constraints, and
//! tradeoff handling. See spec `objectives`.

pub mod evaluator;
pub mod metric_name;
pub mod spec;
pub mod validation;

pub use evaluator::{evaluate, EvaluationOutcome};
pub use metric_name::{is_valid_metric, metric_value, ENGINE_METRICS};
pub use spec::{
    Comparison, ComparisonOp, FoldScheme, Folds, ObjectiveSpec, PrimaryMetric, SecondaryMetric,
    SecondaryMode, Tradeoff,
};
pub use validation::{validate, ValidationError};
