//! Engine-side progress event emission.
//!
//! Emits `tracing` events on `target = "progress"` so the orchestrator
//! can route them through its `ProgressBus` while leaving the rest of
//! the `tracing` stream (RUST_LOG=debug, regular info logs) untouched.
//!
//! Source-side coalescing for hot loops is implemented as a tiny
//! per-path inline coalescer; callers in the per-bar loop instantiate
//! one [`TickCoalescer`] per phase and call [`TickCoalescer::tick`] on
//! every iteration. Within a 250 ms window the highest `current` value
//! is kept and only the boundary event is published.
//!
//! Event shape on the wire (one JSON record per emission, via the
//! tracing JSON layer):
//!
//! ```json
//! {"timestamp": "...", "level": "INFO", "target": "progress",
//!  "fields": {"kind": "phase_begin", "path": "worker.batch_3.run_0",
//!             "total": 5000}}
//! ```
//!
//! The orchestrator-side bridge in `python/strategy_gpt/progress/bridge.py`
//! unwraps the tracing envelope, lifts `fields` into a flat record, and
//! deserializes via `event_from_dict`.

use std::time::{Duration, Instant};

use tracing::info;

/// Source-side coalescing window — matches the spec contract.
pub const COALESCE_WINDOW: Duration = Duration::from_millis(250);

/// Inline coalescer for a single hot-loop path. Not thread-safe; one
/// per emitter site.
pub struct TickCoalescer {
    path: String,
    last_emit: Option<Instant>,
}

impl TickCoalescer {
    pub fn new(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            last_emit: None,
        }
    }

    /// Record a tick; emit a `phase_progress` event if 250 ms have
    /// elapsed since the last emission for this path (or if this is the
    /// first tick). Returns `true` when an event was emitted.
    pub fn tick(&mut self, current: u64, total: Option<u64>) -> bool {
        let now = Instant::now();
        let due = match self.last_emit {
            None => true,
            Some(prev) => now.duration_since(prev) >= COALESCE_WINDOW,
        };
        if !due {
            return false;
        }
        self.last_emit = Some(now);
        emit_progress(&self.path, current, total);
        true
    }

    /// Force-emit the final tick irrespective of the coalescing window.
    /// Use right before a `phase_end` so the renderer's last `current`
    /// is accurate.
    pub fn flush(&mut self, current: u64, total: Option<u64>) {
        self.last_emit = Some(Instant::now());
        emit_progress(&self.path, current, total);
    }
}

/// Emit a `phase_begin` event.
pub fn emit_begin(path: &str, total: Option<u64>) {
    match total {
        Some(t) => info!(target: "progress", kind = "phase_begin", path, total = t),
        None => info!(target: "progress", kind = "phase_begin", path),
    }
}

/// Emit a `phase_progress` event.
pub fn emit_progress(path: &str, current: u64, total: Option<u64>) {
    match total {
        Some(t) => info!(target: "progress", kind = "phase_progress", path, current, total = t),
        None => info!(target: "progress", kind = "phase_progress", path, current),
    }
}

/// Emit a `phase_end` event with the given status (one of "ok", "fail",
/// "skip", "cancelled") and elapsed wall-clock seconds.
pub fn emit_end(path: &str, status: &str, wall_secs: f64) {
    info!(target: "progress", kind = "phase_end", path, status, wall_secs);
}
