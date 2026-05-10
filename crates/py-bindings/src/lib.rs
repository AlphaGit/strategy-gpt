use pyo3::prelude::*;

#[pymodule]
fn strategy_gpt_native(_py: Python<'_>, _m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
