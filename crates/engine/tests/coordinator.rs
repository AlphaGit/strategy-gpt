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
use engine::spec::{
    BatchSpec, DatasetRef, EngineConfig, Mode, RunSpec, StrategyArtifactRef, TimeRange,
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
    assert_eq!(results[0].meta.dataset_manifest, "manifest_hash");
    assert_eq!(results[0].meta.strategy_artifact, "example_noop_artifact");
    assert!(
        !results[0].regimes.is_empty(),
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
        assert_eq!(
            r.meta.seed, runs[i].seed,
            "result {i} must correspond to run {i} (seed match)"
        );
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
