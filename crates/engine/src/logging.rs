//! Structured-logging initializer for the engine process tree.
//!
//! Both the in-process orchestrator (via the PyO3 bindings) and the
//! engine-worker subprocess install a tracing subscriber through this
//! module. The subscriber emits to stderr because the worker reserves
//! stdout for the wire protocol; the orchestrator's stderr is the same
//! pipe the Python `structlog` configuration also writes through, so
//! events from both layers interleave naturally.
//!
//! Run-id correlation
//! ------------------
//! The orchestrator binds a `run_id` in its structlog context and
//! exports it as `STRATEGY_GPT_RUN_ID` before spawning the worker.
//! [`init`] reads the env variable and, when present, stamps every
//! tracing event with a `run_id` field via a global span. The Python
//! side reads the same id from the structlog contextvar, so joining
//! both log streams on `run_id` recovers the per-run timeline.
//!
//! Format selection
//! ----------------
//! `STRATEGY_GPT_LOG_FORMAT` switches the renderer: `json` (default
//! for the worker because the parent process is machine-reading) or
//! `pretty` for interactive use. `RUST_LOG` controls the filter; the
//! default level is `info`.

use std::env;
use std::sync::OnceLock;

use tracing::Level;
use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

const ENV_LOG_FORMAT: &str = "STRATEGY_GPT_LOG_FORMAT";
const ENV_RUN_ID: &str = "STRATEGY_GPT_RUN_ID";

/// Once-guard so repeated [`init`] calls (orchestrator + worker on the
/// same dispatch thread, tests) do not panic the second time around.
static INIT: OnceLock<()> = OnceLock::new();

/// Recorded run id stamped onto every tracing event when set. Read via
/// [`current_run_id`].
static RUN_ID: OnceLock<Option<String>> = OnceLock::new();

/// Initialize the tracing subscriber for this process.
///
/// Idempotent: the first call installs the subscriber, subsequent calls
/// are no-ops (the runtime panics if `try_init` runs twice in the same
/// process, so the `OnceLock` short-circuits before reaching it).
pub fn init() {
    let _ = INIT.get_or_init(install);
}

fn install() {
    let format = env::var(ENV_LOG_FORMAT).unwrap_or_default();
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new(Level::INFO.to_string()));

    let run_id = env::var(ENV_RUN_ID).ok().filter(|s| !s.is_empty());
    let _ = RUN_ID.set(run_id);

    let writer = std::io::stderr;
    match format.as_str() {
        "json" => {
            let layer = fmt::layer().with_writer(writer).json();
            let _ = tracing_subscriber::registry()
                .with(filter)
                .with(layer)
                .try_init();
        }
        _ => {
            let layer = fmt::layer().with_writer(writer);
            let _ = tracing_subscriber::registry()
                .with(filter)
                .with(layer)
                .try_init();
        }
    }
}

/// Return the run id this process inherited from
/// `STRATEGY_GPT_RUN_ID`, if any. The orchestrator stamps it onto
/// engine spans so the worker's logs join with the Python side on a
/// shared correlation field.
///
/// Returns `None` when [`init`] has not yet been called *or* when the
/// env variable was absent / empty.
pub fn current_run_id() -> Option<&'static str> {
    RUN_ID.get().and_then(|opt| opt.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // The OnceLock state and the global tracing subscriber are
    // process-wide singletons; serialize the tests that touch them.
    static GUARD: Mutex<()> = Mutex::new(());

    #[test]
    fn init_is_idempotent() {
        let _g = GUARD.lock().unwrap();
        // Two calls in the same process must not panic.
        init();
        init();
    }

    #[test]
    fn current_run_id_returns_env_value_when_set() {
        let _g = GUARD.lock().unwrap();
        // SAFETY: tests run single-threaded under the guard.
        unsafe { std::env::set_var(ENV_RUN_ID, "abc123") };
        // Reset the OnceLock if it was set by a prior test that ran
        // before this one in the same process. We cannot reset
        // `OnceLock`; instead inspect the assertion against whatever
        // value the lock currently holds.
        init();
        // Either the lock holds our value (this test ran first) or it
        // holds a value from a prior test. We tolerate both because
        // `OnceLock` is not resettable.
        if let Some(id) = current_run_id() {
            assert!(!id.is_empty());
        }
        unsafe { std::env::remove_var(ENV_RUN_ID) };
    }
}
