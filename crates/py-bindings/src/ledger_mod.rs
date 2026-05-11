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

use engine::spec::{BatchSpec, DatasetRef, Mode, RunSpec, StrategyArtifactRef};
use ledger::{
    records::{
        DatasetManifestRecord, DecisionRecord, DivergenceWarning, HypothesisRecord,
        ObjectiveRecord, RunRecord, StrategyVersionRecord,
    },
    Ledger,
};
use pyo3::prelude::*;
use std::sync::{Arc, Mutex};

use crate::gateway::PyDataGateway;
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

    fn get_dataset_manifest(&self, hash: &str) -> PyResult<Option<String>> {
        let l = self.inner.lock().map_err(runtime_err)?;
        let r = l.get_dataset_manifest(hash).map_err(runtime_err)?;
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

    /// Reconstruct the `BatchSpec` + dataset for a recorded run and return
    /// them as a JSON envelope. Drives the
    /// `experiment-ledger::reproducibility-from-ledger-alone` scenario: the
    /// ledger together with the local cache is sufficient to byte-
    /// identically reproduce the run.
    ///
    /// Returns JSON `{ batch_spec, bars, manifest_hash, warnings, run }`.
    /// Replay uses `parallelism = 1` regardless of how the original batch
    /// was submitted; per-run output is invariant to worker count.
    fn replay_run(&self, gateway: &PyDataGateway, run_id: &str) -> PyResult<String> {
        let ledger = self.inner.lock().map_err(runtime_err)?;
        let run = ledger
            .get_run(run_id)
            .map_err(runtime_err)?
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "run `{run_id}` not found in ledger"
                ))
            })?;
        let manifest_rec = ledger
            .get_dataset_manifest(&run.dataset_manifest_hash)
            .map_err(runtime_err)?
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "dataset manifest `{}` not found",
                    run.dataset_manifest_hash
                ))
            })?;
        drop(ledger);

        let gateway_handle = gateway.handle();
        let gw = gateway_handle.lock().map_err(runtime_err)?;
        let dataset = gw
            .load_dataset_from_manifest(&manifest_rec.manifest)
            .map_err(runtime_err)?;
        drop(gw);

        if dataset.manifest_hash != manifest_rec.hash {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                "dataset manifest hash mismatch on replay: \
                 recorded={} reconstructed={}",
                manifest_rec.hash, dataset.manifest_hash
            )));
        }

        let modes: Vec<Mode> = serde_json::from_value(run.modes.clone()).map_err(json_err)?;
        let batch_spec = BatchSpec {
            strategy: StrategyArtifactRef(run.strategy_artifact.clone()),
            dataset: DatasetRef(run.dataset_manifest_hash.clone()),
            runs: vec![RunSpec {
                params: run.parameters.clone(),
                modes,
                seed: run.seed,
                slice: run.slice,
            }],
            engine: run.engine_config.clone(),
            parallelism: 1,
        };

        let payload = serde_json::json!({
            "batch_spec": batch_spec,
            "bars": dataset.bars,
            "manifest_hash": dataset.manifest_hash,
            "warnings": dataset.warnings,
            "run": run,
        });
        serde_json::to_string(&payload).map_err(runtime_err)
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
