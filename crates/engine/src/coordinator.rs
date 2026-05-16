//! Subprocess coordinator for the backtest engine.
//!
//! Spawns one [`engine-worker`] subprocess per [`RunSpec`] in a [`BatchSpec`],
//! dispatched through a parallelism-capped thread pool. Per-run time caps are
//! enforced parent-side by polling `try_wait` and `kill`-ing children that
//! exceed the budget; per-run memory caps are passed to the child via the
//! `STRATEGY_GPT_MEM_BYTES` env var (see `engine-worker` for the setrlimit
//! semantics). Batches abort on the first run failure per the engine spec's
//! `Abort-on-failure` requirement; on abort, every still-running child is
//! killed before [`Coordinator::execute`] returns.
//!
//! Crash isolation is provided by the OS process boundary: a panic, abort, or
//! OOM in a loaded strategy plugin terminates only the worker. The coordinator
//! observes a non-zero exit, packages it as a structured
//! [`CoordinatorError::WorkerFailed`] (wrapping the underlying cause), and
//! reports back to the caller.
//!
//! Post-run regime annotation is invariant under run params (it depends only
//! on the input bars), so the coordinator computes [`annotate_regimes`] once
//! and stamps it onto every result.

use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use engine_rt::Bar;

use crate::regime::annotate_regimes;
use crate::result::{BacktestResult, RunResult};
use crate::spec::{BatchSpec, FailureMode, RunSpec};
use crate::wire::{read_message, write_message, WireError, WorkerRequest, WorkerResponse};

/// Per-run resource caps the coordinator enforces.
///
/// `time` is enforced parent-side by polling. `mem_bytes` is forwarded to the
/// worker via env and applied with `setrlimit`; the spec considers it
/// best-effort (see worker docs).
#[derive(Clone, Copy, Debug)]
pub struct ResourceCaps {
    pub time: Duration,
    pub mem_bytes: Option<u64>,
}

impl Default for ResourceCaps {
    fn default() -> Self {
        Self {
            time: Duration::from_secs(60 * 30),
            mem_bytes: None,
        }
    }
}

/// Coordinator handle: a path to the `engine-worker` binary plus dispatch
/// settings. Cheap to clone.
#[derive(Clone, Debug)]
pub struct Coordinator {
    pub worker_path: PathBuf,
    pub caps: ResourceCaps,
    /// Polling interval for parent-side status / cap checks. 20 ms is fast
    /// enough that time-cap kills fire within a bar of accuracy without
    /// burning CPU.
    pub poll_interval: Duration,
    /// Extra env vars layered on top of the parent environment when spawning
    /// each worker. Used in production to forward orchestrator-level
    /// configuration and in tests to drive the worker's `STRATEGY_GPT_TEST_*`
    /// hooks (sleep, panic) without contaminating the parent's env.
    pub extra_env: HashMap<String, String>,
}

impl Coordinator {
    pub fn new(worker_path: impl Into<PathBuf>) -> Self {
        Self {
            worker_path: worker_path.into(),
            caps: ResourceCaps::default(),
            poll_interval: Duration::from_millis(20),
            extra_env: HashMap::new(),
        }
    }

    pub fn with_caps(mut self, caps: ResourceCaps) -> Self {
        self.caps = caps;
        self
    }

    pub fn with_poll_interval(mut self, interval: Duration) -> Self {
        self.poll_interval = interval;
        self
    }

    /// Extend the per-worker env. Replaces any previously set keys.
    pub fn with_env<I, K, V>(mut self, vars: I) -> Self
    where
        I: IntoIterator<Item = (K, V)>,
        K: Into<String>,
        V: Into<String>,
    {
        for (k, v) in vars {
            self.extra_env.insert(k.into(), v.into());
        }
        self
    }

    /// Execute every run in `batch` against `bars`. Returns one
    /// [`RunResult`] per submitted run in submission-index order.
    ///
    /// Under [`FailureMode::Abort`] (the default) the first failure cancels
    /// remaining runs and surfaces as a [`CoordinatorError::WorkerFailed`];
    /// runs that completed before the abort are dropped to preserve the
    /// loud-failure contract of `strategy-gpt run`. Under
    /// [`FailureMode::Continue`] every run is dispatched and per-run
    /// failures are recorded as [`RunResult::Failed`] entries in the
    /// returned list.
    ///
    /// `artifact_path` is the filesystem path to the strategy `cdylib` the
    /// workers should load. The caller resolves
    /// `BatchSpec.strategy.0` (an opaque identifier — typically a build
    /// pipeline `ArtifactKey` hex hash) to a concrete path before invoking.
    ///
    /// `cancel`, when set to `true`, signals cooperative cancellation: any
    /// already-running child is killed and pending runs are skipped. The
    /// function returns [`CoordinatorError::Cancelled`] regardless of
    /// `failure_mode` — cancellation is a batch-level event, not a run
    /// failure.
    pub fn execute(
        &self,
        batch: &BatchSpec,
        bars: &[Bar],
        artifact_path: &Path,
        dataset_manifest: &str,
        cancel: Option<Arc<AtomicBool>>,
    ) -> Result<Vec<RunResult>, CoordinatorError> {
        if batch.runs.is_empty() {
            return Ok(Vec::new());
        }
        let cancel = cancel.unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
        let abort = Arc::new(AtomicBool::new(false));
        let first_error: Arc<Mutex<Option<CoordinatorError>>> = Arc::new(Mutex::new(None));
        let failure_mode = batch.failure_mode;

        // Queue of (index, run) work items. Threads pop from the front; on
        // failure under `Abort` they drain the queue so no further work
        // starts, while under `Continue` they keep pulling.
        let queue: Arc<Mutex<VecDeque<(usize, RunSpec)>>> = Arc::new(Mutex::new(
            batch
                .runs
                .iter()
                .cloned()
                .enumerate()
                .collect::<VecDeque<_>>(),
        ));
        let results: Arc<Mutex<Vec<Option<RunResult>>>> =
            Arc::new(Mutex::new((0..batch.runs.len()).map(|_| None).collect()));

        // Worker-pool size capped to exactly `parallelism`; never spawn
        // more threads even when many runs are pending.
        let parallelism = batch.parallelism.max(1).min(batch.runs.len());
        let artifact_path_str = artifact_path.to_string_lossy().into_owned();

        let mut handles = Vec::with_capacity(parallelism);
        for _ in 0..parallelism {
            let queue = Arc::clone(&queue);
            let results = Arc::clone(&results);
            let cancel = Arc::clone(&cancel);
            let abort = Arc::clone(&abort);
            let first_error = Arc::clone(&first_error);
            let worker_path = self.worker_path.clone();
            let caps = self.caps;
            let poll_interval = self.poll_interval;
            let extra_env = self.extra_env.clone();
            let bars = bars.to_vec();
            let engine = batch.engine.clone();
            let strategy_artifact = batch.strategy.0.clone();
            let manifest = dataset_manifest.to_string();
            let artifact_path_str = artifact_path_str.clone();

            handles.push(thread::spawn(move || {
                while !abort.load(Ordering::Relaxed) && !cancel.load(Ordering::Relaxed) {
                    let item = {
                        let mut q = queue.lock().expect("queue mutex");
                        q.pop_front()
                    };
                    let Some((idx, run)) = item else {
                        break;
                    };
                    let request = WorkerRequest {
                        artifact_path: artifact_path_str.clone(),
                        run,
                        bars: bars.clone(),
                        engine: engine.clone(),
                        strategy_artifact: strategy_artifact.clone(),
                        dataset_manifest: manifest.clone(),
                    };
                    match run_one_subprocess(
                        &worker_path,
                        &request,
                        caps,
                        poll_interval,
                        &extra_env,
                        &cancel,
                        &abort,
                    ) {
                        Ok(result) => {
                            let mut r = results.lock().expect("results mutex");
                            r[idx] = Some(RunResult::ok(idx, *result));
                        }
                        Err(CoordinatorError::Cancelled) => {
                            // Cancellation is a batch-level event; the
                            // outer cancel branch reports it.
                            break;
                        }
                        Err(e) => match failure_mode {
                            FailureMode::Abort => {
                                abort.store(true, Ordering::Relaxed);
                                let mut slot = first_error.lock().expect("first_error mutex");
                                if slot.is_none() {
                                    *slot = Some(CoordinatorError::WorkerFailed {
                                        run_index: idx,
                                        source: Box::new(e),
                                    });
                                }
                                break;
                            }
                            FailureMode::Continue => {
                                let (kind, message) = classify_failure(&e);
                                let mut r = results.lock().expect("results mutex");
                                r[idx] = Some(RunResult::failed(idx, kind, message));
                            }
                        },
                    }
                }
            }));
        }

        for h in handles {
            // A panic inside the worker thread (not the child process) is a
            // bug in the coordinator itself, not strategy code; convert to
            // a coordinator error rather than crashing.
            if let Err(panic) = h.join() {
                let msg = panic_message(panic);
                abort.store(true, Ordering::Relaxed);
                let mut slot = first_error.lock().expect("first_error mutex");
                if slot.is_none() {
                    *slot = Some(CoordinatorError::DispatchPanic { message: msg });
                }
            }
        }

        if cancel.load(Ordering::Relaxed) {
            // Cancellation may race with a recorded error: prefer the cancel
            // signal — the caller asked us to stop and is not interested in
            // post-mortem run details.
            return Err(CoordinatorError::Cancelled);
        }
        if let Some(err) = first_error.lock().expect("first_error mutex").take() {
            return Err(err);
        }

        // Annotate regimes once; stamp on every successful run.
        let regimes = annotate_regimes(bars);
        let collected: Vec<RunResult> = results
            .lock()
            .expect("results mutex")
            .iter_mut()
            .enumerate()
            .map(|(idx, slot)| {
                let mut r = slot
                    .take()
                    .expect("every run must have produced an outcome when no abort was recorded");
                debug_assert_eq!(
                    r.run_index(),
                    idx,
                    "submission-index aggregation must align each slot",
                );
                if let RunResult::Ok { result, .. } = &mut r {
                    result.regimes.clone_from(&regimes);
                }
                r
            })
            .collect();
        Ok(collected)
    }
}

/// Classify a [`CoordinatorError`] into the `(error_kind, message)` pair
/// stored on [`RunResult::Failed`]. Stable across reruns: the kind label
/// is fixed per variant and the message is sourced from the error's own
/// `Display`, which inherits determinism from the underlying worker
/// channel.
fn classify_failure(err: &CoordinatorError) -> (&'static str, String) {
    let kind = match err {
        CoordinatorError::WorkerExited { .. } => "worker_exited",
        CoordinatorError::WorkerReported(_) => "worker_reported",
        CoordinatorError::TimeCapExceeded { .. } => "time_cap_exceeded",
        CoordinatorError::MemCapExceeded { .. } => "mem_cap_exceeded",
        CoordinatorError::Io(_) => "io",
        CoordinatorError::Wire(_) => "wire",
        CoordinatorError::Spawn { .. } => "spawn",
        CoordinatorError::DispatchPanic { .. } => "dispatch_panic",
        CoordinatorError::WorkerFailed { .. } => "worker_failed",
        CoordinatorError::Cancelled => "cancelled",
    };
    (kind, format!("{err}"))
}

/// Errors returned by [`Coordinator::execute`].
#[derive(Debug, thiserror::Error)]
pub enum CoordinatorError {
    #[error("run {run_index} failed: {source}")]
    WorkerFailed {
        run_index: usize,
        #[source]
        source: Box<CoordinatorError>,
    },
    #[error("worker exited with code {code:?}, stderr: {stderr}")]
    WorkerExited { code: Option<i32>, stderr: String },
    #[error("worker reported error: {0}")]
    WorkerReported(String),
    #[error("worker exceeded time cap of {cap:?}")]
    TimeCapExceeded { cap: Duration },
    #[error("worker exceeded memory cap of {cap} bytes (killed by OS)")]
    MemCapExceeded { cap: u64 },
    #[error("cancelled by caller")]
    Cancelled,
    #[error("worker io: {0}")]
    Io(#[from] std::io::Error),
    #[error("worker wire protocol: {0}")]
    Wire(#[from] WireError),
    #[error("worker spawn `{path}`: {source}")]
    Spawn {
        path: String,
        #[source]
        source: std::io::Error,
    },
    #[error("dispatch thread panicked: {message}")]
    DispatchPanic { message: String },
}

/// Outcome of the parent-side wait loop.
enum WaitOutcome {
    Exited(ExitStatus),
    TimedOut,
    Cancelled,
}

fn run_one_subprocess(
    worker_path: &Path,
    request: &WorkerRequest,
    caps: ResourceCaps,
    poll_interval: Duration,
    extra_env: &HashMap<String, String>,
    cancel: &Arc<AtomicBool>,
    abort: &Arc<AtomicBool>,
) -> Result<Box<BacktestResult>, CoordinatorError> {
    let mut cmd = Command::new(worker_path);
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(bytes) = caps.mem_bytes {
        cmd.env("STRATEGY_GPT_MEM_BYTES", bytes.to_string());
    }
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    let mut child = cmd.spawn().map_err(|source| CoordinatorError::Spawn {
        path: worker_path.display().to_string(),
        source,
    })?;

    // Write the request synchronously. The pipe buffer comfortably holds the
    // request payloads we care about (bars + spec), so this rarely blocks;
    // if it does, the child is reading and will drain.
    {
        let mut stdin = child
            .stdin
            .take()
            .expect("piped stdin must be present after spawn");
        write_message(&mut stdin, request)?;
        let _ = stdin.flush();
        // Drop closes the pipe, signalling EOF to the child.
    }

    // Drain stdout / stderr on background threads so the kernel buffers
    // never fill (which would deadlock the child).
    let stdout = child
        .stdout
        .take()
        .expect("piped stdout must be present after spawn");
    let stderr = child
        .stderr
        .take()
        .expect("piped stderr must be present after spawn");
    let stdout_handle = thread::spawn(move || drain_to_vec(stdout));
    let stderr_handle = thread::spawn(move || drain_to_vec(stderr));

    let outcome = wait_with_caps(&mut child, caps, poll_interval, cancel, abort)?;

    let stdout_bytes = stdout_handle.join().unwrap_or_default();
    let stderr_bytes = stderr_handle.join().unwrap_or_default();

    let exit_status = match outcome {
        WaitOutcome::Cancelled => return Err(CoordinatorError::Cancelled),
        WaitOutcome::TimedOut => return Err(CoordinatorError::TimeCapExceeded { cap: caps.time }),
        WaitOutcome::Exited(status) => status,
    };

    if !exit_status.success() {
        let stderr_text = String::from_utf8_lossy(&stderr_bytes).into_owned();
        // OOM kill on Linux surfaces as SIGKILL (exit code None on Unix when
        // killed by signal). Report that distinctly when a mem cap was set.
        if let Some(bytes) = caps.mem_bytes {
            if exit_status.code().is_none() {
                return Err(CoordinatorError::MemCapExceeded { cap: bytes });
            }
        }
        // Worker emits a `WorkerResponse::Error` on stdout before exiting
        // non-zero for in-band failures (plugin load, run_one error); prefer
        // that message if we can parse one.
        if let Ok(WorkerResponse::Error { message }) =
            read_message::<_, WorkerResponse>(&mut std::io::Cursor::new(stdout_bytes.as_slice()))
        {
            return Err(CoordinatorError::WorkerReported(message));
        }
        return Err(CoordinatorError::WorkerExited {
            code: exit_status.code(),
            stderr: stderr_text,
        });
    }

    let response: WorkerResponse =
        read_message(&mut std::io::Cursor::new(stdout_bytes.as_slice()))?;
    match response {
        WorkerResponse::Ok { result } => Ok(result),
        WorkerResponse::Error { message } => Err(CoordinatorError::WorkerReported(message)),
    }
}

fn wait_with_caps(
    child: &mut std::process::Child,
    caps: ResourceCaps,
    poll_interval: Duration,
    cancel: &Arc<AtomicBool>,
    abort: &Arc<AtomicBool>,
) -> Result<WaitOutcome, CoordinatorError> {
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(WaitOutcome::Exited(status));
        }
        if cancel.load(Ordering::Relaxed) || abort.load(Ordering::Relaxed) {
            let _ = child.kill();
            let _ = child.wait();
            return Ok(WaitOutcome::Cancelled);
        }
        if start.elapsed() > caps.time {
            let _ = child.kill();
            let _ = child.wait();
            return Ok(WaitOutcome::TimedOut);
        }
        thread::sleep(poll_interval);
    }
}

fn drain_to_vec<R: std::io::Read>(mut reader: R) -> Vec<u8> {
    let mut buf = Vec::new();
    let _ = reader.read_to_end(&mut buf);
    buf
}

fn panic_message(payload: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = payload.downcast_ref::<&'static str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "<non-string panic payload>".to_string()
    }
}
