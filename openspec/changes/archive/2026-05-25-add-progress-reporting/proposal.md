## Why

Long-running CLI commands (`optimize`, `run --wait`, `hypothesize`, `tester`, `fetch`) currently emit nothing visible between submit and terminal status. A 4-hour optimization is indistinguishable from a hung process; a yfinance retry loop and a healthy fold both look like a black screen. structlog/tracing records exist but go to stderr as JSON, which is unreadable at the terminal and easy to mistake for noise. Users cannot tell whether progress is being made, where the workload is stuck, or how much remains.

## What Changes

- Define a typed `ProgressEvent` vocabulary (`phase_begin`, `phase_progress`, `phase_end`, `heartbeat`) with a dotted-path identity (e.g. `optimize.fold_2.trial_47`) and an optional `current/total` count.
- Add a Python `ProgressBus` that the orchestrator and LangGraph nodes emit into directly; wire it into `optimization_runner`, `hypothesis_loop`, `tester`, `smoke`, `cli.run --wait`, and `Gateway.fetch`.
- Bridge Rust worker progress over stderr: workers emit `tracing` events on `target="progress"`; the orchestrator parses them and re-emits as `ProgressEvent`s on the same bus.
- Add a `--progress {auto,plain,json,off}` CLI flag (default `auto`). TTY → rich `Live` renderer with a phase tree + nested bars on stderr; non-TTY → one JSONL line per event on stderr.
- Source-side coalescing: per-bar / per-trial loops emit at most once per 250ms; heartbeat every 5s on any phase with no event so consumers can distinguish "in-progress" from "hung."
- SIGINT drains the renderer (final state visible) before tearing down workers; progress events are never persisted to the experiment ledger (UX channel, not record-of-truth).
- Add `rich` as a runtime dependency (publishing date ≥7 days verified at install time).

## Capabilities

### New Capabilities
- `progress-reporting`: typed phase-tree progress event vocabulary, the orchestrator-side bus, the rich `Live` TTY renderer, the JSONL sink, the Rust→Python stderr bridge, and the CLI `--progress` flag contract.

### Modified Capabilities
<!-- None. Existing capabilities continue to satisfy their requirements; this change adds a new
     observability surface that wraps them rather than altering their behavior. -->

## Impact

- **Python**: new `python/strategy_gpt/progress.py` (bus, events, sinks); call-site instrumentation in `optimization_runner.py`, `hypothesis_loop.py`, `tester.py`, `smoke.py`, `gateway.py`, `cli.py`. New `--progress` option on long-running commands.
- **Rust**: new `tracing` events with `target="progress"` in `crates/engine/src/coordinator.rs` (batch begin/end, per-run completion) and `crates/engine/src/worker.rs` (per-bar coalesced ticks). No new IPC channels; reuses existing stderr stream.
- **Dependencies**: adds `rich` to `python/pyproject.toml`. No new Rust crates.
- **Ledger**: explicitly out of scope — progress events MUST NOT be written to the ledger; reproducibility surface unchanged.
- **Tests**: progress events are deterministic given the same input; existing byte-identity smoke tests are unaffected (events go to stderr, results to stdout).
- **CI**: `--progress=auto` detects non-TTY and uses JSONL; existing CI log capture continues to work.
