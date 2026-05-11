//! PyO3 bindings exposing the trusted Rust crates to the Python orchestrator.
//!
//! Boundary convention: complex types travel as JSON strings (`serde_json`),
//! handles travel as opaque `pyclass` wrappers. Python passes/receives `dict`
//! after a `json.loads` / `json.dumps`. Keeps the FFI surface narrow and
//! frees us from per-type `pyclass` bookkeeping; conversion cost is acceptable
//! at orchestrator-call granularity.
//!
//! Module name: `strategy_gpt_native` (matched by `[lib]` in Cargo.toml).
//! Submodules: `gateway`, `ledger`, `objectives`.

use pyo3::exceptions::{PyIOError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;

mod gateway;
mod ledger_mod;
mod objectives_mod;

#[pymodule]
fn strategy_gpt_native(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    let g = PyModule::new_bound(py, "gateway")?;
    g.add_class::<gateway::PyDataGateway>()?;
    m.add_submodule(&g)?;

    let l = PyModule::new_bound(py, "ledger")?;
    l.add_class::<ledger_mod::PyLedger>()?;
    m.add_submodule(&l)?;

    let o = PyModule::new_bound(py, "objectives")?;
    o.add_function(wrap_pyfunction!(objectives_mod::validate_spec, &o)?)?;
    o.add_function(wrap_pyfunction!(objectives_mod::evaluate_spec, &o)?)?;
    o.add_function(wrap_pyfunction!(objectives_mod::engine_metrics, &o)?)?;
    m.add_submodule(&o)?;

    Ok(())
}

pub(crate) fn json_err<E: std::fmt::Display>(e: E) -> PyErr {
    PyValueError::new_err(format!("invalid JSON payload: {e}"))
}

pub(crate) fn io_err<E: std::fmt::Display>(e: E) -> PyErr {
    PyIOError::new_err(format!("{e}"))
}

pub(crate) fn runtime_err<E: std::fmt::Display>(e: E) -> PyErr {
    PyRuntimeError::new_err(format!("{e}"))
}
