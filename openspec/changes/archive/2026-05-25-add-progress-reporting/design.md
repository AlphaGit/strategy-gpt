## Context

Strategy-GPT runs LLM-driven research loops where most commands take minutes to hours: `optimize` walks N folds × M trials × per-trial backtests; `hypothesize` iterates diagnose → kb_query → generate → critique; `tester` lints + builds + smoke + full batch; even `fetch` may stall on a provider retry. The current observability surface is structlog JSON to stderr on the Python side and `tracing` JSON to stderr on the Rust side, joined by `run_id`. That is excellent for post-hoc analysis and terrible for the live experience — at the terminal, a user sees nothing between submit and a final status, with no way to distinguish "alive but slow" from "hung." There is no instrumentation in `optimization_runner.py`, `hypothesis_loop.py`, `tester.py`, `smoke.py`, or the Rust worker; the engine coordinator polls workers every 20 ms internally but emits no events outward.

Two audiences want different things from the same underlying state: a human at a TTY wants "alive? where are you? roughly how long?"; a CI log or downstream tool wants a complete, machine-parseable record of every phase transition. Both must come from the same source of truth, never drift, and never compete with the JSON-record-of-truth that already flows to stderr.

## Goals / Non-Goals

**Goals:**
- A typed, dotted-path phase-tree event vocabulary that fits every long-running command (`optimize`, `hypothesize`, `tester`, `smoke`, `run --wait`, `fetch`).
- A heartbeat signal that makes "still alive" unambiguous after any silent interval ≥ 5 s.
- A `rich.live` TTY renderer with a phase tree and nested bars, throttled to ≤ 10 Hz, that auto-detects non-TTY and degrades to a JSONL line per event.
- A Rust → Python bridge that reuses the existing worker stderr stream (no new IPC channels, no wire-format changes).
- Source-side coalescing so hot loops (per-bar, per-trial) cannot emit more than ~4 events/second per phase.
- Clean cancellation: SIGINT flushes the final renderer state before tearing down workers.

**Non-Goals:**
- Persisting progress events to the experiment ledger. Reproducibility is unaffected; progress is strictly a UX channel.
- Changing the `BacktestResult` schema, the worker wire protocol, or the ledger schema.
- Cross-process progress for the LangGraph subgraph beyond the orchestrator's own process tree (no distributed dashboard).
- Reproducing structlog functionality. Progress events are coarse, throttled, and human-facing; full structured records continue to flow through structlog/tracing alongside.
- Token-level streaming of LLM output. The `generate` phase surfaces the most recent reasoning summary as `latest_msg`, not the token stream.

## Decisions

### Event vocabulary

Four event kinds, all keyed by a dotted `path`:

| Kind | Required fields | Optional fields | Emitted when |
|------|-----------------|-----------------|--------------|
| `phase_begin` | `path`, `started_at` | `total`, `unit`, `msg` | entering a named phase |
| `phase_progress` | `path`, `current` | `total`, `msg`, `metrics` | tick within a phase |
| `phase_end` | `path`, `status` (`ok`/`fail`/`skip`), `wall_secs` | `msg`, `metrics` | leaving a phase |
| `heartbeat` | `path`, `wall_secs`, `since_last_event_secs` | `msg` | phase open with no event for ≥ 5 s |

`path` examples: `optimize.fold_2.search`, `optimize.fold_2.trial_124`, `hypothesize.generate`, `tester.smoke`, `worker.batch_7.bar_4500`.

Rationale: a tree gives natural nesting for the actual command shapes; dotted strings are trivially filterable, sortable, and grep-friendly when they end up in CI logs. `total` is optional because some searches (CMA-ES until convergence) have no a priori bound; the renderer shows elapsed + rate when `total` is `None`. `metrics` is a small `dict[str, float]` (e.g. `{"best_sharpe": 1.41, "current_sharpe": 1.38}`) that the renderer surfaces inline.

Alternatives considered: flat phase names with a `parent` field (rejected — extra indirection, less greppable); fixing a closed enum of phase names (rejected — every new strategy or search method would force a vocabulary patch).

### Transport: worker stderr with a structured `target`

Rust workers emit events via `tracing::info!(target = "progress", path = "...", ...)`. The existing `crates/engine/src/logging.rs` subscriber already routes these to stderr as JSON. The Python orchestrator reads the worker's stderr line-by-line (it already pipes it), parses each JSON record, and:
- if `target == "progress"`, deserialize into a `ProgressEvent` and publish to the `ProgressBus`
- otherwise, forward verbatim to the existing structlog stream

Rationale: the worker already writes JSON to stderr, the orchestrator already reads it, and `target` is a stable `tracing` field. No new socket, no Arrow IPC change, no protocol versioning. Filtering by `target` keeps `RUST_LOG=debug` noise out of the progress stream.

Alternatives considered: piggyback on the Arrow result IPC (rejected — couples progress to the result wire format, which is versioned and load-bearing); dedicated Unix-domain socket (rejected — extra plumbing, harder on Windows/CI).

### Renderer: `rich.live` + `rich.progress` + `rich.tree`

The TTY renderer wraps a `rich.live.Live` driving a `rich.tree.Tree` of phase nodes. Each open phase with a known `total` gets a `rich.progress.Progress` task; each open phase without a total renders as a `rich.spinner.Spinner` plus elapsed seconds. `Console(stderr=True, force_terminal=auto)` keeps stdout free for JSON results.

Rationale: phase tree maps 1:1 to `rich.tree.Tree`; nested progress is a first-class concept; TTY autodetection is built in; ~1 MB pure-Python wheel. The `update-config` no-dep alternative (custom ANSI over stderr) would cost ~200 LOC and reinvent SIGWINCH/resize handling.

Alternatives considered: `tqdm` (poor multi-bar interleaving with stderr logs), `enlighten` (no tree primitive), `textual` (full-screen TUI, overkill), zero-dep custom ANSI (maintenance burden, Windows edge cases).

### Sinks: pluggable, one selected per process

The `ProgressBus` fan-outs events to a sink list. The CLI flag `--progress {auto,plain,json,off}` chooses one:
- `auto` (default): `RichLiveSink` if `sys.stderr.isatty()`, else `JsonlSink`.
- `plain`: one human-readable line per `phase_begin`/`phase_end` + every 30 s heartbeat, no ANSI. For `tee`-into-a-file workflows.
- `json`: one JSON line per event on stderr, no throttling at the sink. For machine consumers.
- `off`: silent (events still flow through structlog at INFO; progress sink discards).

### Coalescing: source-side, fixed 250 ms window

Hot loops (per-bar update inside a backtest, per-trial completion inside a fold) call `bus.tick(path, current)` which internally drops repeat ticks within the last 250 ms for that `path`, keeping the highest `current`. `phase_begin` and `phase_end` are never dropped. The 250 ms bound gives ≤ 4 events/sec/phase, comfortably under the renderer's 10 Hz refresh and well within JSONL line-rate budgets.

### Heartbeat: per-phase 5 s timer

A single background asyncio task in the orchestrator scans open phases every 1 s. For any phase whose `last_event_ts` is older than 5 s, it synthesizes a `heartbeat` event with `since_last_event_secs`. The heartbeat is suppressed if the phase ends in the same tick. This guarantees a non-TTY consumer sees a line at least every 5 s per active phase.

### Cancellation

The orchestrator installs a SIGINT handler that:
1. Flips a `cancelled` flag the renderer reads.
2. Calls `bus.flush()` to drain the event queue and let the renderer emit a final `phase_end(status=cancelled)` per open phase.
3. Returns control to the existing teardown path (which signals workers and waits for them).

The renderer's `Live` context manager exits cleanly before the orchestrator propagates the interrupt, so the user sees a final state instead of a half-redrawn frame.

### Hypothesis-loop disclosure

The `hypothesize.generate` phase emits a `phase_progress` event per LangGraph node transition and on each new reasoning summary from the LLM. The most recent summary (truncated to ~120 chars) rides in `msg`. No PII filtering is applied — this is a research tool used by the strategy designer; raw model output at the terminal is acceptable. Full reasoning continues to land in structlog records for post-hoc analysis.

## Risks / Trade-offs

- **[Worker stderr coupled to UX]** → Mitigation: filter strictly on `target == "progress"`; ignore unknown record shapes; keep the rest of structlog/tracing flowing as before so a malformed progress line never silences logs.
- **[`rich` is a new heavy-ish dependency]** → Mitigation: pin a version ≥ 7 days old (supply-chain rule); confine imports to `progress.py` so a missing `rich` only breaks the renderer, not the orchestrator; verify pure-Python install on the target Python versions in CI.
- **[Renderer-rate vs log-rate]** → Mitigation: dual-budget — source-side 250 ms coalescing for emitters, sink-side ≤ 10 Hz refresh for the TTY renderer; JSONL sink writes every event because logs are searchable.
- **[Stdout contamination of JSON output]** → Mitigation: the renderer constructs `Console(stderr=True)`; `cli.py` already writes results to stdout, progress to stderr; tested explicitly in a smoke test that pipes stdout to a JSON parser.
- **[Determinism / reproducibility]** → Mitigation: progress events are pure side-effects on stderr, never inputs; the ledger reproducibility surface is unchanged; the smoke byte-identity test continues to compare results on stdout.
- **[Cross-process event ordering]** → Mitigation: every event carries a monotonic source clock (`emitted_at`); the bus does not attempt to re-sort but the dotted `path` prefix (`worker.*` vs `orch.*`) makes the origin unambiguous in any log archive.
- **[CI noise]** → Mitigation: `auto` selects JSONL on non-TTY; line rate is bounded by 250 ms coalescing + 5 s heartbeat; users can pass `--progress=off` in CI when even structured lines are unwanted.

## Migration Plan

1. Land `progress.py` (bus, events, sinks) behind a feature gate (`--progress=off` is the default until rollout completes).
2. Instrument `optimization_runner.py` end-to-end first; this is the loudest source today and validates the design under load.
3. Add the Rust `target="progress"` events in `crates/engine/src/coordinator.rs` and `crates/engine/src/worker.rs`, then the orchestrator-side parser.
4. Instrument `hypothesis_loop.py`, `tester.py`, `smoke.py`, `gateway.py`.
5. Flip the default to `--progress=auto`.

No rollback complexity: every change is additive; remove the flag default flip to revert UX without touching call sites.

## Open Questions

None blocking. Two style-level questions deferred until first review of the renderer prototype:

- Final color palette / phase-status glyphs (✓/✗/⠋/…). Default to `rich` built-ins; tweak after seeing live output.
- Whether `--progress=auto` should silence structlog INFO records to stderr while the renderer is active (to avoid log/UI interleaving). Initial answer: no, keep both flowing; revisit if it's unreadable in practice.
