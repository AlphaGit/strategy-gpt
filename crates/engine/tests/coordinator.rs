//! Integration tests for `engine::coordinator`.
//!
//! Build the real `engine-worker` binary and the `example-strategy` cdylib,
//! then drive `Coordinator::execute` over the same kinds of `BatchSpec`s the
//! orchestrator submits in production. Coverage:
//!
//! - end-to-end happy path with a single run
//! - parallel dispatch across multiple runs preserves order
//! - per-run time cap kills a slow worker (`STRATEGY_GPT_TEST_SLEEP_MS`)
//! - worker panic surfaces as `CoordinatorError::WorkerFailed` without
//!   crashing the coordinator thread (process isolation)
//! - abort-on-failure stops a batch after the first run failure
//! - cancel flag aborts in-flight runs
//! - empty batch returns an empty `Vec`
//!
//! Tests are sequential by default (`cargo test` parallelises across files
//! but tests inside one file share the binary handle — fine since each test
//! gets its own [`Coordinator`]).

use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use chrono::{TimeZone, Utc};
use engine::coordinator::{Coordinator, CoordinatorError, ResourceCaps};
use engine::result::RunResult;
use engine::spec::{
    BatchSpec, DatasetRef, EngineConfig, FailureMode, Mode, RunSpec, StrategyArtifactRef, TimeRange,
};
use engine_rt::{Bar, Resolution};

fn worker_bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_engine-worker"))
}

fn build_example_strategy() -> PathBuf {
    let status = Command::new(env!("CARGO"))
        .args(["build", "-p", "example-strategy"])
        .status()
        .expect("invoking `cargo build -p example-strategy`");
    assert!(status.success(), "cargo build -p example-strategy failed");
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let workspace_target = manifest_dir
        .parent()
        .expect("engine crate has a parent dir")
        .join("target");
    let dylib_name = if cfg!(target_os = "windows") {
        "example_strategy.dll"
    } else if cfg!(target_os = "macos") {
        "libexample_strategy.dylib"
    } else {
        "libexample_strategy.so"
    };
    let path = workspace_target.join("debug").join(dylib_name);
    assert!(
        path.exists(),
        "expected example-strategy artifact at {}",
        path.display()
    );
    path
}

fn day_bars(n: usize) -> Vec<Bar> {
    (0..n)
        .map(|i| Bar {
            symbol: "VXX".into(),
            ts: Utc.with_ymd_and_hms(2024, 1, 1, 0, 0, 0).unwrap()
                + chrono::Duration::days(i as i64),
            resolution: Resolution::Day,
            open: 100.0 + i as f64,
            high: 101.0 + i as f64,
            low: 99.0 + i as f64,
            close: 100.0 + i as f64,
            volume: 1_000.0,
        })
        .collect()
}

fn run_spec(start: chrono::DateTime<Utc>, end: chrono::DateTime<Utc>, seed: u64) -> RunSpec {
    RunSpec {
        params: serde_json::json!({}),
        modes: vec![Mode::Plain],
        seed,
        slice: TimeRange { start, end },
    }
}

fn batch_spec(runs: Vec<RunSpec>, parallelism: usize) -> BatchSpec {
    BatchSpec {
        strategy: StrategyArtifactRef("example_noop_artifact".into()),
        dataset: DatasetRef("manifest_hash".into()),
        runs,
        engine: EngineConfig::default(),
        parallelism,
        failure_mode: FailureMode::Abort,
    }
}

fn unwrap_ok(r: &RunResult) -> &engine::BacktestResult {
    match r {
        RunResult::Ok { result, .. } => result.as_ref(),
        RunResult::Failed {
            run_index,
            error_kind,
            message,
        } => panic!(
            "expected Ok at index {run_index}, got Failed kind={error_kind} message={message}",
        ),
    }
}

fn standard_setup() -> (Vec<Bar>, PathBuf, Coordinator, TimeRange) {
    let bars = day_bars(30);
    let strategy = build_example_strategy();
    let coordinator = Coordinator::new(worker_bin());
    let slice = TimeRange {
        start: bars.first().unwrap().ts,
        end: bars.last().unwrap().ts + chrono::Duration::days(1),
    };
    (bars, strategy, coordinator, slice)
}

#[test]
fn coordinator_runs_single_batch_end_to_end() {
    let (bars, strategy, coordinator, slice) = standard_setup();
    let spec = batch_spec(vec![run_spec(slice.start, slice.end, 1)], 1);
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("execute");
    assert_eq!(results.len(), 1);
    let r0 = unwrap_ok(&results[0]);
    assert_eq!(r0.meta.dataset_manifest, "manifest_hash");
    assert_eq!(r0.meta.strategy_artifact, "example_noop_artifact");
    assert!(
        !r0.regimes.is_empty(),
        "regime annotation should fire over 30 bars"
    );
}

#[test]
fn coordinator_preserves_run_order_with_parallelism() {
    let (bars, strategy, coordinator, slice) = standard_setup();
    let runs: Vec<RunSpec> = (0..4)
        .map(|i| run_spec(slice.start, slice.end, 100 + i as u64))
        .collect();
    let spec = batch_spec(runs.clone(), 4);
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("execute");
    assert_eq!(results.len(), 4);
    for (i, r) in results.iter().enumerate() {
        let inner = unwrap_ok(r);
        assert_eq!(
            inner.meta.seed, runs[i].seed,
            "result {i} must correspond to run {i} (seed match)"
        );
        assert_eq!(r.run_index(), i, "RunResult.run_index aligns with slot");
    }
}

#[test]
fn coordinator_time_cap_kills_slow_worker() {
    let (bars, strategy, _, slice) = standard_setup();
    let coordinator = Coordinator::new(worker_bin())
        .with_caps(ResourceCaps {
            time: Duration::from_millis(150),
            mem_bytes: None,
        })
        .with_poll_interval(Duration::from_millis(10))
        .with_env([("STRATEGY_GPT_TEST_SLEEP_MS", "5000")]);
    let spec = batch_spec(vec![run_spec(slice.start, slice.end, 1)], 1);
    let err = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect_err("must time out");
    match err {
        CoordinatorError::WorkerFailed { run_index, source } => {
            assert_eq!(run_index, 0);
            assert!(
                matches!(*source, CoordinatorError::TimeCapExceeded { .. }),
                "expected nested TimeCapExceeded, got {source:?}"
            );
        }
        other => panic!("expected WorkerFailed, got {other:?}"),
    }
}

#[test]
fn coordinator_surfaces_worker_panic_as_failure() {
    let (bars, strategy, _, slice) = standard_setup();
    let coordinator = Coordinator::new(worker_bin()).with_env([("STRATEGY_GPT_TEST_PANIC", "1")]);
    let spec = batch_spec(vec![run_spec(slice.start, slice.end, 1)], 1);
    let err = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect_err("worker panic must surface as error");
    match err {
        CoordinatorError::WorkerFailed { run_index, source } => {
            assert_eq!(run_index, 0);
            // Panic produces a non-zero exit (likely 101 on stable Rust) with
            // no in-band WorkerResponse — so the parent reports WorkerExited.
            assert!(
                matches!(*source, CoordinatorError::WorkerExited { .. }),
                "expected WorkerExited, got {source:?}"
            );
        }
        other => panic!("expected WorkerFailed, got {other:?}"),
    }
    // Coordinator (and this test thread) must still be alive — implicit by
    // reaching this line. Explicit re-execute on a fresh coordinator confirms
    // no global state was wedged: the parent didn't crash, the worker bin is
    // still usable, and a clean run succeeds.
    let healthy = Coordinator::new(worker_bin());
    let results = healthy
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("recovery run must succeed after a peer worker panic");
    assert_eq!(results.len(), 1);
}

#[test]
fn coordinator_aborts_batch_on_first_failure() {
    let (bars, strategy, _, slice) = standard_setup();
    let coordinator =
        Coordinator::new(worker_bin()).with_env([("STRATEGY_GPT_TEST_EXIT_CODE", "7")]);
    let runs: Vec<RunSpec> = (0..6)
        .map(|i| run_spec(slice.start, slice.end, 200 + i as u64))
        .collect();
    let spec = batch_spec(runs, 2);
    let err = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect_err("must abort");
    match err {
        CoordinatorError::WorkerFailed { source, .. } => {
            assert!(
                matches!(
                    *source,
                    CoordinatorError::WorkerExited { code: Some(7), .. }
                ),
                "expected exit code 7, got {source:?}"
            );
        }
        other => panic!("expected WorkerFailed, got {other:?}"),
    }
}

#[test]
fn coordinator_cancel_aborts_in_flight() {
    let (bars, strategy, _, slice) = standard_setup();
    let coordinator = Coordinator::new(worker_bin())
        .with_poll_interval(Duration::from_millis(10))
        .with_env([("STRATEGY_GPT_TEST_SLEEP_MS", "5000")]);
    let cancel = Arc::new(AtomicBool::new(false));
    let spec = batch_spec(
        vec![
            run_spec(slice.start, slice.end, 1),
            run_spec(slice.start, slice.end, 2),
        ],
        2,
    );
    let cancel_clone = Arc::clone(&cancel);
    // Trip the cancel flag shortly after dispatch begins.
    let signal = thread::spawn(move || {
        thread::sleep(Duration::from_millis(120));
        cancel_clone.store(true, Ordering::Relaxed);
    });
    let err = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", Some(cancel))
        .expect_err("cancel must abort");
    signal.join().expect("signal thread");
    assert!(
        matches!(err, CoordinatorError::Cancelled),
        "expected Cancelled, got {err:?}"
    );
}

#[test]
fn coordinator_empty_batch_returns_empty_vec() {
    let (bars, strategy, coordinator, _) = standard_setup();
    let spec = batch_spec(vec![], 1);
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("execute");
    assert!(results.is_empty());
}

#[test]
fn coordinator_aggregates_in_submission_order_under_out_of_order_completion() {
    // Forty runs across eight workers with a uniform sleep make completion
    // order non-deterministic; the FIFO dispatcher + indexed slot
    // aggregation must still hand results back in submission order.
    let (bars, strategy, _, slice) = standard_setup();
    let coordinator = Coordinator::new(worker_bin())
        .with_poll_interval(Duration::from_millis(5))
        .with_env([("STRATEGY_GPT_TEST_SLEEP_MS", "30")]);
    let n = 40;
    let runs: Vec<RunSpec> = (0..n)
        .map(|i| run_spec(slice.start, slice.end, 5_000 + i as u64))
        .collect();
    let spec = batch_spec(runs.clone(), 8);
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("execute");
    assert_eq!(results.len(), n);
    for (i, r) in results.iter().enumerate() {
        assert_eq!(r.run_index(), i, "result at slot {i} is misaligned");
        let inner = unwrap_ok(r);
        assert_eq!(inner.meta.seed, runs[i].seed, "seed match at index {i}");
    }
}

fn continue_batch(runs: Vec<RunSpec>, parallelism: usize) -> BatchSpec {
    let mut spec = batch_spec(runs, parallelism);
    spec.failure_mode = FailureMode::Continue;
    spec
}

#[test]
fn coordinator_continue_mode_isolates_per_run_failures() {
    let (bars, strategy, _, slice) = standard_setup();
    // 1,000-run packed batch with failures at indices 0, 499, 999. Each run's
    // seed encodes its index so the worker test hook can fail-by-seed.
    let n: usize = 1_000;
    let runs: Vec<RunSpec> = (0..n)
        .map(|i| run_spec(slice.start, slice.end, 1_000 + i as u64))
        .collect();
    let fail_seeds = "1000,1499,1999";
    let coordinator = Coordinator::new(worker_bin())
        .with_poll_interval(Duration::from_millis(2))
        .with_env([("STRATEGY_GPT_TEST_FAIL_SEEDS", fail_seeds)]);
    let spec = continue_batch(runs, 8);
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("continue mode must not surface batch-level error");
    assert_eq!(results.len(), n);
    let failed_indices: Vec<usize> = results
        .iter()
        .filter_map(|r| match r {
            RunResult::Failed { run_index, .. } => Some(*run_index),
            RunResult::Ok { .. } => None,
        })
        .collect();
    assert_eq!(
        failed_indices,
        vec![0, 499, 999],
        "failures must land at the injected indices"
    );
    for r in &results {
        match r {
            RunResult::Ok { .. } => {}
            RunResult::Failed {
                error_kind,
                message,
                ..
            } => {
                assert_eq!(
                    error_kind, "worker_exited",
                    "failure kind stable across reruns"
                );
                assert!(
                    message.contains("11"),
                    "message should preserve worker exit code 11, got {message}",
                );
            }
        }
    }
}

#[test]
fn coordinator_continue_mode_is_deterministic_across_reruns() {
    let (bars, strategy, _, slice) = standard_setup();
    let n = 200;
    let runs: Vec<RunSpec> = (0..n)
        .map(|i| run_spec(slice.start, slice.end, 9_000 + i as u64))
        .collect();
    let coordinator = Coordinator::new(worker_bin())
        .with_poll_interval(Duration::from_millis(2))
        .with_env([("STRATEGY_GPT_TEST_FAIL_SEEDS", "9000,9100,9199")]);
    let spec = continue_batch(runs, 4);
    let run = || {
        coordinator
            .execute(&spec, &bars, &strategy, "manifest_hash", None)
            .expect("execute")
    };
    let first = run();
    let second = run();
    assert_eq!(first.len(), second.len());
    for (a, b) in first.iter().zip(second.iter()) {
        match (a, b) {
            (
                RunResult::Failed {
                    run_index: ai,
                    error_kind: ak,
                    message: am,
                },
                RunResult::Failed {
                    run_index: bi,
                    error_kind: bk,
                    message: bm,
                },
            ) => {
                assert_eq!(ai, bi);
                assert_eq!(ak, bk);
                assert_eq!(am, bm);
            }
            (RunResult::Ok { run_index: ai, .. }, RunResult::Ok { run_index: bi, .. }) => {
                assert_eq!(ai, bi);
            }
            _ => panic!("rerun produced a different outcome shape at index"),
        }
    }
}

// 10,000-run smoke: validates the artifact-compile-once promise and that
// the worker pool saturates at `parallelism` without leaking handles.
// Marked `#[ignore]` so it is opt-in (`cargo test -- --ignored`) — the run
// is throughput-sensitive and CPU-bound.
#[test]
#[ignore = "large packed-batch smoke; opt-in with --ignored"]
fn coordinator_large_packed_batch_smoke() {
    let (bars, strategy, _, slice) = standard_setup();
    let n: usize = 10_000;
    let runs: Vec<RunSpec> = (0..n)
        .map(|i| run_spec(slice.start, slice.end, 20_000 + i as u64))
        .collect();
    let parallelism = 8;
    let coordinator = Coordinator::new(worker_bin()).with_poll_interval(Duration::from_millis(2));
    let spec = continue_batch(runs, parallelism);
    let started = std::time::Instant::now();
    let results = coordinator
        .execute(&spec, &bars, &strategy, "manifest_hash", None)
        .expect("execute");
    let elapsed = started.elapsed();
    assert_eq!(results.len(), n);
    assert!(
        results.iter().all(|r| matches!(r, RunResult::Ok { .. })),
        "10k-run smoke must produce only Ok outcomes",
    );
    for (i, r) in results.iter().enumerate() {
        assert_eq!(r.run_index(), i, "index alignment lost at {i}");
    }
    // Loose throughput floor: 10 runs per second per worker is a generous
    // lower bound for a 30-bar no-op strategy; the test fails fast if a
    // regression breaks the artifact-compile-once contract or the worker
    // pool sizing.
    let upper_bound = Duration::from_secs((n as u64) / (parallelism as u64) / 10);
    assert!(
        elapsed < upper_bound,
        "10k-run smoke too slow ({:?} >= {:?}); compile-once may be broken",
        elapsed,
        upper_bound
    );
}
