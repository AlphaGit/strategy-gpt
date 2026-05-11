//! PyO3 bindings for `ledger::Ledger`.
//!
//! Surface:
//! - `PyLedger(root: str)` — open (and `CREATE TABLE IF NOT EXISTS`) a ledger.
//! - `record_run / record_hypothesis / record_decision / record_divergence /
//!    record_dataset_manifest / record_objective / record_strategy_version` —
//!    JSON-string in, no result on success.
//! - `get_run(id: str) -> str | None` — JSON-encoded `RunRecord` or `None`.
//! - `recent_decisions(limit: int) -> str` — JSON-encoded array of
//!   `RecentDecision`.
//! - Sidecar I/O routed through `Ledger::sidecars` for trade/signal/equity/
//!   exec-log lists keyed by run id.

use ledger::{
    records::{
        DatasetManifestRecord, DecisionRecord, DivergenceWarning, HypothesisRecord,
        ObjectiveRecord, RunRecord, StrategyVersionRecord,
    },
    Ledger,
};
use pyo3::prelude::*;
use std::sync::{Arc, Mutex};

use crate::{json_err, runtime_err};

#[pyclass(module = "strategy_gpt_native.ledger", name = "Ledger", unsendable)]
pub struct PyLedger {
    inner: Arc<Mutex<Ledger>>,
}

#[pymethods]
impl PyLedger {
    #[new]
    fn new(root: &str) -> PyResult<Self> {
        let l = Ledger::open(root).map_err(runtime_err)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(l)),
        })
    }

    fn root(&self) -> PyResult<String> {
        let l = self.inner.lock().map_err(runtime_err)?;
        Ok(l.root().display().to_string())
    }

    fn record_run(&self, record_json: &str) -> PyResult<()> {
        let r: RunRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_run(&r)
            .map_err(runtime_err)
    }

    fn record_hypothesis(&self, record_json: &str) -> PyResult<()> {
        let r: HypothesisRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_hypothesis(&r)
            .map_err(runtime_err)
    }

    fn record_decision(&self, record_json: &str) -> PyResult<()> {
        let r: DecisionRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_decision(&r)
            .map_err(runtime_err)
    }

    fn record_dataset_manifest(&self, record_json: &str) -> PyResult<()> {
        let r: DatasetManifestRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_dataset_manifest(&r)
            .map_err(runtime_err)
    }

    fn record_divergence(&self, record_json: &str) -> PyResult<()> {
        let r: DivergenceWarning = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_divergence(&r)
            .map_err(runtime_err)
    }

    fn record_objective(&self, record_json: &str) -> PyResult<()> {
        let r: ObjectiveRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_objective(&r)
            .map_err(runtime_err)
    }

    fn record_strategy_version(&self, record_json: &str) -> PyResult<()> {
        let r: StrategyVersionRecord = serde_json::from_str(record_json).map_err(json_err)?;
        self.inner
            .lock()
            .map_err(runtime_err)?
            .record_strategy_version(&r)
            .map_err(runtime_err)
    }

    fn get_run(&self, id: &str) -> PyResult<Option<String>> {
        let l = self.inner.lock().map_err(runtime_err)?;
        let r = l.get_run(id).map_err(runtime_err)?;
        match r {
            Some(rec) => Ok(Some(serde_json::to_string(&rec).map_err(runtime_err)?)),
            None => Ok(None),
        }
    }

    fn recent_decisions(&self, limit: usize) -> PyResult<String> {
        let l = self.inner.lock().map_err(runtime_err)?;
        let rows = l.recent_decisions(limit).map_err(runtime_err)?;
        serde_json::to_string(&rows).map_err(runtime_err)
    }

    fn store_sidecar(&self, run_id: &str, kind: &str, records_json: &str) -> PyResult<()> {
        let l = self.inner.lock().map_err(runtime_err)?;
        let sidecars = l.sidecars();
        match kind {
            "trades" => {
                let v: Vec<engine::result::Trade> =
                    serde_json::from_str(records_json).map_err(json_err)?;
                sidecars.write_trades(run_id, &v).map_err(runtime_err)?;
            }
            "signals" => {
                let v: Vec<engine_rt::SignalEvent> =
                    serde_json::from_str(records_json).map_err(json_err)?;
                sidecars.write_signals(run_id, &v).map_err(runtime_err)?;
            }
            "equity" => {
                let v: Vec<engine::result::EquityPoint> =
                    serde_json::from_str(records_json).map_err(json_err)?;
                sidecars.write_equity(run_id, &v).map_err(runtime_err)?;
            }
            "exec_log" => {
                let v: Vec<engine_rt::DecisionEvent> =
                    serde_json::from_str(records_json).map_err(json_err)?;
                sidecars.write_exec_log(run_id, &v).map_err(runtime_err)?;
            }
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown sidecar kind `{other}`; expected one of \
                     trades, signals, equity, exec_log"
                )))
            }
        }
        Ok(())
    }

    fn load_sidecar(&self, run_id: &str, kind: &str) -> PyResult<String> {
        let l = self.inner.lock().map_err(runtime_err)?;
        let sidecars = l.sidecars();
        let json = match kind {
            "trades" => serde_json::to_string(&sidecars.read_trades(run_id).map_err(runtime_err)?),
            "signals" => {
                serde_json::to_string(&sidecars.read_signals(run_id).map_err(runtime_err)?)
            }
            "equity" => serde_json::to_string(&sidecars.read_equity(run_id).map_err(runtime_err)?),
            "exec_log" => {
                serde_json::to_string(&sidecars.read_exec_log(run_id).map_err(runtime_err)?)
            }
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown sidecar kind `{other}`; expected one of \
                     trades, signals, equity, exec_log"
                )))
            }
        };
        json.map_err(runtime_err)
    }
}
