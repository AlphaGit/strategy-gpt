//! `engine-worker` — subprocess that runs one [`engine::RunSpec`] inside its
//! own OS process and streams the resulting [`engine::BacktestResult`] back
//! over stdout.
//!
//! Wire protocol: see [`engine::wire`]. Stdin carries exactly one
//! [`engine::wire::WorkerRequest`]; stdout carries exactly one
//! [`engine::wire::WorkerResponse`]. Stderr is reserved for human-readable
//! diagnostics and is not parsed by the coordinator.
//!
//! Resource caps
//! -------------
//! On Unix the worker reads `STRATEGY_GPT_MEM_BYTES`; if set, it calls
//! `setrlimit(RLIMIT_AS, ...)` (and `RLIMIT_DATA` as a fallback for platforms
//! where `RLIMIT_AS` is not honored, such as macOS). The memory cap is
//! best-effort: it constrains the address space the worker can map, which is
//! the canonical OS primitive but is approximate vs. true RSS. The time cap
//! is enforced parent-side by the coordinator and is not handled here.
//!
//! Process isolation
//! -----------------
//! Strategy code runs here, not in the orchestrator. A panic, abort, or OOM
//! in the loaded plugin terminates this process; the coordinator observes a
//! non-zero exit and surfaces a structured failure to the caller.

use std::io::{self, Write};
use std::process::ExitCode;
use std::sync::Arc;

use engine::executor::StrategyFactory;
use engine::indicators::baseline_registry;
use engine::plugin::{PluginFactory, StrategyPlugin};
use engine::wire::{read_message, write_message, WorkerRequest, WorkerResponse};
use engine::{apply_modes, run_one};

fn main() -> ExitCode {
    engine::logging::init();
    apply_resource_limits();
    apply_test_hooks();

    let mut stdin = io::stdin().lock();
    let request: WorkerRequest = match read_message(&mut stdin) {
        Ok(r) => r,
        Err(e) => {
            return emit_response(WorkerResponse::Error {
                message: format!("read request: {e}"),
            })
        }
    };

    let response = match run(request) {
        Ok(result) => WorkerResponse::Ok {
            result: Box::new(result),
        },
        Err(message) => WorkerResponse::Error { message },
    };
    emit_response(response)
}

fn run(request: WorkerRequest) -> Result<engine::BacktestResult, String> {
    let plugin = StrategyPlugin::load(&request.artifact_path)
        .map_err(|e| format!("plugin load `{}`: {e}", request.artifact_path))?;
    let plugin = Arc::new(plugin);
    let factory = PluginFactory(Arc::clone(&plugin));
    let mut strategy = factory.make();

    let mut result = run_one(
        strategy.as_mut(),
        &request.bars,
        &request.run,
        &request.engine,
        baseline_registry(),
        &request.strategy_artifact,
        &request.dataset_manifest,
    )
    .map_err(|e| format!("run_one: {e}"))?;

    apply_modes(
        &mut result,
        &factory,
        &baseline_registry,
        &request.bars,
        &request.run,
        &request.engine,
        &request.strategy_artifact,
        &request.dataset_manifest,
    )
    .map_err(|e| format!("apply_modes: {e}"))?;

    Ok(result)
}

fn emit_response(response: WorkerResponse) -> ExitCode {
    let mut stdout = io::stdout().lock();
    match write_message(&mut stdout, &response) {
        Ok(()) => {
            let _ = stdout.flush();
            match response {
                WorkerResponse::Ok { .. } => ExitCode::SUCCESS,
                WorkerResponse::Error { .. } => ExitCode::from(2),
            }
        }
        Err(e) => {
            // Last-ditch: write to stderr so the coordinator can include it
            // in its non-zero exit diagnostics.
            let _ = writeln!(io::stderr(), "engine-worker: write response failed: {e}");
            ExitCode::from(3)
        }
    }
}

#[cfg(unix)]
fn apply_resource_limits() {
    let Ok(value) = std::env::var("STRATEGY_GPT_MEM_BYTES") else {
        return;
    };
    let Ok(bytes) = value.parse::<u64>() else {
        let _ = writeln!(
            io::stderr(),
            "engine-worker: STRATEGY_GPT_MEM_BYTES `{value}` is not a u64; ignoring"
        );
        return;
    };
    // Try RLIMIT_AS (Linux honors; macOS often ignores) and RLIMIT_DATA in
    // sequence. Both failures are non-fatal — the spec models mem caps as a
    // best-effort guard whose hard backstop is process isolation.
    let limit = libc::rlimit {
        rlim_cur: bytes as libc::rlim_t,
        rlim_max: bytes as libc::rlim_t,
    };
    // SAFETY: `setrlimit` reads a fully-initialized `rlimit` by reference
    // and returns a result code; no aliasing, no UB.
    unsafe {
        let _ = libc::setrlimit(libc::RLIMIT_AS, &limit);
        let _ = libc::setrlimit(libc::RLIMIT_DATA, &limit);
    }
}

#[cfg(not(unix))]
fn apply_resource_limits() {
    // Non-Unix: setrlimit equivalents (Job Objects on Windows) are not wired
    // in v1. The coordinator's time cap still applies; the mem cap is a no-op.
}

/// Test-only hooks driven by env vars. Coordinator integration tests use
/// these to exercise the time-cap, panic, and exit-code paths without a
/// dedicated misbehaving-strategy fixture. Production never sets these.
fn apply_test_hooks() {
    if std::env::var_os("STRATEGY_GPT_TEST_PANIC").is_some() {
        panic!("STRATEGY_GPT_TEST_PANIC set (worker panic test hook)");
    }
    if let Ok(s) = std::env::var("STRATEGY_GPT_TEST_SLEEP_MS") {
        if let Ok(ms) = s.parse::<u64>() {
            std::thread::sleep(std::time::Duration::from_millis(ms));
        }
    }
    if let Ok(s) = std::env::var("STRATEGY_GPT_TEST_EXIT_CODE") {
        if let Ok(code) = s.parse::<u8>() {
            std::process::exit(code as i32);
        }
    }
}
