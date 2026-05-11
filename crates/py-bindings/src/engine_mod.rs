//! PyO3 bindings for the backtest engine's control plane.
//!
//! Surface (matches spec `backtest-engine::PyO3 control-plane bindings`):
//! - `PyEngine()` — open the in-process engine.
//! - `submit_batch(artifact_path, bars_json, spec_json, dataset_manifest)
//!    -> handle: str` — load the plugin, spawn a worker thread that drives
//!   the batch, return an opaque handle the caller can poll.
//! - `poll(handle: str) -> str` — JSON `{ status: "running" | "completed" |
//!   "failed" | "cancelled", results?, error? }`.
//! - `cancel(handle: str) -> bool` — request cooperative cancellation; honored
//!   between runs and between mode iterations (`Ordering::Relaxed`).
//! - `drop_handle(handle: str) -> bool` — release the entry; returns whether
//!   the handle existed.
//!
//! The worker process binary + coordinator (tasks 4.2 / 4.3) supersede the
//! thread-backed implementation here. v1 keeps everything in-process so the
//! Python orchestrator can drive batches end-to-end before the subprocess
//! work lands; the control-plane surface is identical, so the swap is a
//! drop-in replacement at that point.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

use engine::{
    annotate_regimes, apply_modes,
    executor::StrategyFactory,
    indicators::baseline_registry,
    plugin::{PluginFactory, StrategyPlugin},
    run_one,
    spec::BatchSpec,
    BacktestResult,
};
use engine_rt::Bar;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Serialize;
use uuid::Uuid;

use crate::{json_err, runtime_err};

/// Snapshot of job progress. Stored under the engine's handle map; cloned on
/// poll so callers see a consistent view without holding the lock.
enum JobState {
    Running,
    Completed(Vec<BacktestResult>),
    Failed(String),
    Cancelled,
}

struct JobEntry {
    state: JobState,
    cancel: Arc<AtomicBool>,
}

#[pyclass(module = "strategy_gpt_native.engine", name = "Engine", unsendable)]
pub struct PyEngine {
    handles: Arc<Mutex<HashMap<String, JobEntry>>>,
}

#[pymethods]
impl PyEngine {
    #[new]
    fn new() -> Self {
        Self {
            handles: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Validate the spec, register a handle, spawn a worker thread, return id.
    fn submit_batch(
        &self,
        artifact_path: &str,
        bars_json: &str,
        spec_json: &str,
        dataset_manifest: &str,
    ) -> PyResult<String> {
        let bars: Vec<Bar> = serde_json::from_str(bars_json).map_err(json_err)?;
        let spec: BatchSpec = serde_json::from_str(spec_json).map_err(json_err)?;
        let id = Uuid::new_v4().to_string();
        let cancel = Arc::new(AtomicBool::new(false));
        {
            let mut h = self.handles.lock().map_err(runtime_err)?;
            h.insert(
                id.clone(),
                JobEntry {
                    state: JobState::Running,
                    cancel: Arc::clone(&cancel),
                },
            );
        }
        let artifact_path = artifact_path.to_string();
        let manifest = dataset_manifest.to_string();
        let id_clone = id.clone();
        let handles = Arc::clone(&self.handles);
        thread::spawn(move || {
            let outcome = run_job(&artifact_path, bars, spec, &manifest, cancel);
            // Acquire to publish the result. A poisoned mutex here means a
            // previous worker panicked while holding it; recover by stomping
            // through the poison rather than crashing the engine.
            let mut h = match handles.lock() {
                Ok(h) => h,
                Err(p) => p.into_inner(),
            };
            if let Some(entry) = h.get_mut(&id_clone) {
                entry.state = outcome;
            }
        });
        Ok(id)
    }

    /// Snapshot the current state of `handle` as JSON.
    fn poll(&self, handle: &str) -> PyResult<String> {
        let h = self.handles.lock().map_err(runtime_err)?;
        let entry = h
            .get(handle)
            .ok_or_else(|| PyValueError::new_err(format!("unknown handle `{handle}`")))?;
        let payload = match &entry.state {
            JobState::Running => PollPayload::Running { status: "running" },
            JobState::Completed(results) => PollPayload::Completed {
                status: "completed",
                results,
            },
            JobState::Failed(message) => PollPayload::Failed {
                status: "failed",
                error: message.as_str(),
            },
            JobState::Cancelled => PollPayload::Cancelled {
                status: "cancelled",
            },
        };
        serde_json::to_string(&payload).map_err(runtime_err)
    }

    /// Signal cooperative cancellation. Returns whether the job was still
    /// running at the time the signal was sent.
    fn cancel(&self, handle: &str) -> PyResult<bool> {
        let h = self.handles.lock().map_err(runtime_err)?;
        let entry = h
            .get(handle)
            .ok_or_else(|| PyValueError::new_err(format!("unknown handle `{handle}`")))?;
        if matches!(entry.state, JobState::Running) {
            entry.cancel.store(true, Ordering::Relaxed);
            Ok(true)
        } else {
            Ok(false)
        }
    }

    /// Discard the handle's entry. Idempotent: returns `false` if absent.
    fn drop_handle(&self, handle: &str) -> PyResult<bool> {
        let mut h = self.handles.lock().map_err(runtime_err)?;
        Ok(h.remove(handle).is_some())
    }
}

#[derive(Serialize)]
#[serde(untagged)]
enum PollPayload<'a> {
    Running {
        status: &'a str,
    },
    Completed {
        status: &'a str,
        results: &'a [BacktestResult],
    },
    Failed {
        status: &'a str,
        error: &'a str,
    },
    Cancelled {
        status: &'a str,
    },
}

fn run_job(
    artifact_path: &str,
    bars: Vec<Bar>,
    spec: BatchSpec,
    manifest: &str,
    cancel: Arc<AtomicBool>,
) -> JobState {
    let plugin = match StrategyPlugin::load(artifact_path) {
        Ok(p) => Arc::new(p),
        Err(e) => return JobState::Failed(format!("plugin load: {e}")),
    };
    let factory = PluginFactory(Arc::clone(&plugin));
    let make_indicators = || baseline_registry();
    let mut out = Vec::with_capacity(spec.runs.len());
    for (i, run) in spec.runs.iter().enumerate() {
        if cancel.load(Ordering::Relaxed) {
            return JobState::Cancelled;
        }
        let mut strategy = factory.make();
        let mut result = match run_one(
            strategy.as_mut(),
            &bars,
            run,
            &spec.engine,
            make_indicators(),
            &spec.strategy.0,
            manifest,
        ) {
            Ok(r) => r,
            Err(e) => return JobState::Failed(format!("run {i}: {e}")),
        };
        if let Err(e) = apply_modes(
            &mut result,
            &factory,
            &make_indicators,
            &bars,
            run,
            &spec.engine,
            &spec.strategy.0,
            manifest,
        ) {
            return JobState::Failed(format!("run {i} modes: {e}"));
        }
        result.regimes = annotate_regimes(&bars);
        out.push(result);
    }
    JobState::Completed(out)
}
