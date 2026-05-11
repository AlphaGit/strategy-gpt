//! PyO3 bindings for the objectives evaluator.
//!
//! Module-level functions:
//! - `validate_spec(spec_json: str) -> str` — returns JSON
//!   `{"ok": true}` on success or `{"ok": false, "errors": [...]}` listing the
//!   `ValidationError` variants raised by `objectives::validate`.
//! - `evaluate_spec(spec_json: str, metrics_json: str) -> str` — JSON
//!   `EvaluationOutcome { accepted, score, violations, soft_misses }`.
//! - `engine_metrics() -> str` — JSON list of canonical metric names that
//!   spec primaries/secondaries may reference.

use objectives::{evaluate, validate, ENGINE_METRICS};
use pyo3::prelude::*;
use serde::Serialize;

use crate::{json_err, runtime_err};

#[pyfunction]
pub fn validate_spec(spec_json: &str) -> PyResult<String> {
    let spec: objectives::ObjectiveSpec = serde_json::from_str(spec_json).map_err(json_err)?;
    let report = match validate(&spec) {
        Ok(()) => ValidationReport {
            ok: true,
            errors: Vec::new(),
        },
        Err(e) => ValidationReport {
            ok: false,
            errors: vec![e.to_string()],
        },
    };
    serde_json::to_string(&report).map_err(runtime_err)
}

#[pyfunction]
pub fn evaluate_spec(spec_json: &str, metrics_json: &str) -> PyResult<String> {
    let spec: objectives::ObjectiveSpec = serde_json::from_str(spec_json).map_err(json_err)?;
    let metrics: engine::result::BacktestMetrics =
        serde_json::from_str(metrics_json).map_err(json_err)?;
    let outcome = evaluate(&metrics, &spec);
    serde_json::to_string(&outcome).map_err(runtime_err)
}

#[pyfunction]
pub fn engine_metrics() -> PyResult<String> {
    serde_json::to_string(&ENGINE_METRICS).map_err(runtime_err)
}

#[derive(Serialize)]
struct ValidationReport {
    ok: bool,
    errors: Vec<String>,
}
