//! PyO3 bindings for the backtest engine's control plane.
//!
//! Surface (matches spec `backtest-engine::PyO3 control-plane bindings`):
//! - `PyEngine(worker_path: str)` — open the engine with a concrete path to
//!   the `engine-worker` binary the [`Coordinator`] will spawn for every run.
//! - `submit_batch(artifact_path, bars_json, spec_json, dataset_manifest)
//!    -> handle: str` — register the request, spawn a dispatch thread that
//!   drives the coordinator, return an opaque handle the caller can poll.
//! - `poll(handle: str) -> str` — JSON `{ status: "running" | "completed" |
//!   "failed" | "cancelled", results?, error? }`.
//! - `cancel(handle: str) -> bool` — request cooperative cancellation;
//!   honored between runs and parent-side while polling worker subprocesses.
//! - `drop_handle(handle: str) -> bool` — release the entry; returns whether
//!   the handle existed.
//!
//! Strategy execution lives in the [`engine-worker`] subprocess (tasks
//! 4.2/4.3), enforcing the spec's `Worker process isolation` requirement: a
//! panic, OOM, or timeout in strategy code never crosses the process
//! boundary into the orchestrator.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use engine::coordinator::{Coordinator, CoordinatorError, ResourceCaps};
use engine::{spec::BatchSpec, BacktestResult};
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
    worker_path: PathBuf,
    /// Optional per-run time cap. `None` falls back to [`ResourceCaps`]
    /// defaults (30 min) — orchestrator overrides via the constructor.
    caps: ResourceCaps,
    handles: Arc<Mutex<HashMap<String, JobEntry>>>,
}

#[pymethods]
impl PyEngine {
    /// Open an engine handle.
    ///
    /// `worker_path` — filesystem path to the compiled `engine-worker`
    /// binary the coordinator spawns per run. Required.
    ///
    /// `time_cap_secs` — per-run wall-clock cap, in seconds. Defaults to the
    /// engine spec's 30-minute conservative ceiling.
    ///
    /// `mem_cap_bytes` — per-run address-space cap; forwarded to the worker
    /// via `setrlimit`. Optional (default: unbounded). See
    /// `engine-worker` docs for cross-platform caveats.
    #[new]
    #[pyo3(signature = (worker_path, time_cap_secs = None, mem_cap_bytes = None))]
    fn new(
        worker_path: &str,
        time_cap_secs: Option<f64>,
        mem_cap_bytes: Option<u64>,
    ) -> PyResult<Self> {
        let worker_path = PathBuf::from(worker_path);
        if !worker_path.exists() {
            return Err(PyValueError::new_err(format!(
                "worker binary `{}` does not exist",
                worker_path.display()
            )));
        }
        let mut caps = ResourceCaps::default();
        if let Some(secs) = time_cap_secs {
            if !secs.is_finite() || secs <= 0.0 {
                return Err(PyValueError::new_err(
                    "time_cap_secs must be a positive finite number",
                ));
            }
            caps.time = Duration::from_secs_f64(secs);
        }
        caps.mem_bytes = mem_cap_bytes;
        Ok(Self {
            worker_path,
            caps,
            handles: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    /// Path to the `engine-worker` binary this engine dispatches to.
    #[getter]
    fn worker_path(&self) -> String {
        self.worker_path.display().to_string()
    }

    /// Validate the spec, register a handle, spawn a dispatch thread, return
    /// the opaque handle id.
    ///
    /// `run_id`, when provided, is exported as `STRATEGY_GPT_RUN_ID` on
    /// every worker subprocess so the Rust tracing layer stamps it onto
    /// each event. The orchestrator's structlog context binds the same
    /// id; both log streams join on `run_id` for cross-process
    /// correlation (task 13.2).
    #[pyo3(signature = (artifact_path, bars_json, spec_json, dataset_manifest, run_id=None))]
    fn submit_batch(
        &self,
        artifact_path: &str,
        bars_json: &str,
        spec_json: &str,
        dataset_manifest: &str,
        run_id: Option<&str>,
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
        let artifact_path = PathBuf::from(artifact_path);
        let manifest = dataset_manifest.to_string();
        let mut coordinator = Coordinator::new(self.worker_path.clone()).with_caps(self.caps);
        if let Some(rid) = run_id.filter(|s| !s.is_empty()) {
            coordinator = coordinator.with_env([("STRATEGY_GPT_RUN_ID", rid.to_string())]);
        }
        let id_clone = id.clone();
        let handles = Arc::clone(&self.handles);
        thread::spawn(move || {
            let outcome = match coordinator.execute(
                &spec,
                &bars,
                &artifact_path,
                &manifest,
                Some(Arc::clone(&cancel)),
            ) {
                Ok(results) => JobState::Completed(results),
                Err(CoordinatorError::Cancelled) => JobState::Cancelled,
                Err(e) => JobState::Failed(format!("{e}")),
            };
            // A poisoned mutex here means a previous dispatch thread panicked
            // while holding it; recover by stomping through the poison rather
            // than crashing the engine.
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
