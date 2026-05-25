## 1. Dependencies and scaffolding

- [x] 1.1 Add `rich` to `python/pyproject.toml` runtime deps; pin a version ≥ 7 days old; refresh `requirements*.txt` / lockfiles
- [x] 1.2 Create `python/strategy_gpt/progress/__init__.py` package with submodule layout: `events.py`, `bus.py`, `sinks/`, `bridge.py`
- [x] 1.3 Add a `--progress` Typer option helper in `cli.py` (`auto|plain|json|off`, default `auto`) and a shared resolver that returns the configured sink list

## 2. Event model and bus

- [x] 2.1 Define `ProgressEvent` dataclasses in `progress/events.py`: `PhaseBegin`, `PhaseProgress`, `PhaseEnd`, `Heartbeat`; all carry `path: str`, `emitted_at: float` (monotonic), plus kind-specific fields per the spec
- [x] 2.2 Implement `progress/bus.py` `ProgressBus` with `emit()`, `begin()`, `tick()`, `end()` helpers, an `asyncio.Queue` for fan-out, and a `flush()` method
- [x] 2.3 Implement 250 ms source-side coalescing inside `bus.tick()` keyed by `path`, preserving the highest `current` value in each window; cover with unit tests asserting boundary events are never dropped
- [x] 2.4 Implement the 5 s heartbeat scanner as a background `asyncio` task that walks open phases each second and synthesizes `Heartbeat` events; suppress when a `PhaseEnd` fires in the same tick

## 3. Sinks

- [x] 3.1 Implement `progress/sinks/jsonl.py` that writes one JSON line per event to `sys.stderr`; one event per line, no buffering beyond the stdlib default
- [x] 3.2 Implement `progress/sinks/plain.py` for `--progress=plain`: human-readable line per `phase_begin`/`phase_end`, throttle heartbeats to ≤ 1 per 30 s, no ANSI
- [x] 3.3 Implement `progress/sinks/rich_live.py` using `rich.live.Live` over `Console(stderr=True, force_terminal=auto)`, driving a `rich.tree.Tree` of phase nodes; refresh ≤ 10 Hz
- [x] 3.4 In `rich_live.py`, render phases with known `total` as `rich.progress.Progress` tasks and phases without `total` as `rich.spinner.Spinner` + elapsed seconds; surface `metrics` and `msg` fields inline
- [x] 3.5 Implement the `auto` resolver: `RichLiveSink` if `sys.stderr.isatty()`, else `JsonlSink`; verify `--progress=off` installs no sink while leaving structlog untouched

## 4. Rust worker → orchestrator bridge

- [x] 4.1 In `crates/engine/src/coordinator.rs`, add `tracing::info!(target = "progress", ...)` events at batch begin, per-run begin/end, and batch end with stable `path` strings under the `worker.batch_<n>.*` prefix
- [x] 4.2 In `crates/engine/src/worker.rs` (per-bar loop), emit coalesced `tracing::info!(target = "progress", path = "worker.batch_<n>.run_<m>.bars", current = ...)` ticks; coalesce at the emit site at 250 ms granularity
- [x] 4.3 In `python/strategy_gpt/progress/bridge.py`, add a stderr line reader that consumes worker stderr, parses each JSON record, publishes records with `target == "progress"` as `ProgressEvent`s, and forwards all other records to the existing structlog stream
- [x] 4.4 Wire the bridge into `python/strategy_gpt/engine.py` so every worker process spawned by `Engine.submit_batch` is read by the bridge; ensure unparseable progress lines log a structured warning and do not silence subsequent lines

## 5. Orchestrator instrumentation

- [x] 5.1 Instrument `optimization_runner.run_optimization` with `phase_begin/end` at the optimization, fold, and search-method levels; emit `phase_progress` per trial completion with `metrics={"score": ..., "best": ...}`
- [x] 5.2 Instrument the cross-fold OOS validation step in `optimization_runner` with its own `phase_begin/end` pair under `optimize.oos`
- [x] 5.3 Instrument `hypothesis_loop.py` with `phase_begin/end` per LangGraph node (`diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`); emit `phase_progress` with truncated reasoning summary in `msg` on each new interim LLM output
- [x] 5.4 Instrument `tester.py` with sub-phases `tester.lint`, `tester.build`, `tester.smoke`, `tester.full_batch`
- [x] 5.5 Instrument `smoke.py` with `phase_begin/end` at the top level and per-run `phase_progress`
- [x] 5.6 Instrument `gateway.py` `Gateway.fetch` with `fetch.<provider>.download` (and `.parse`, `.cache_write` if visible)
- [x] 5.7 Update `cli.py run --wait` poll loop to read events from the bridge and bus instead of being silent; ensure stdout still receives only the final JSON

## 6. Lifecycle, cancellation, ledger guarantee

- [x] 6.1 Install a SIGINT handler in `cli.py` (or shared entrypoint) that flips a `cancelled` flag, calls `ProgressBus.flush()`, synthesizes `phase_end(status="cancelled")` for every open phase, lets the active sink render its final state, then propagates to the existing teardown path
- [x] 6.2 Audit `optimization_ledger.py` and `ledger.py` write paths; add a unit test asserting that no `ProgressEvent` payload, heartbeat record, or path string is written to ledger rows or parquet sidecars
- [x] 6.3 Add a byte-identity test that runs an optimization once with `--progress=auto` (TTY emulator) and once with `--progress=off`, asserting the ledger rows and parquet sidecars are identical

## 7. Tests

- [x] 7.1 Unit tests for `ProgressEvent` serialization round-trip (Python ↔ JSONL ↔ Rust `tracing` JSON shape)
- [x] 7.2 Unit tests for the bus: coalescing within 250 ms, boundary events not dropped, heartbeat timing, flush semantics
- [x] 7.3 Integration test: spawn a fake worker that writes a known sequence of `target="progress"` and non-progress stderr lines; assert the bridge routes them correctly and unparseable lines warn but do not poison the stream
- [x] 7.4 Renderer test (capture `rich.Console` to a `StringIO`): assert refresh-rate cap, spinner-vs-bar selection based on `total`, stdout cleanliness
- [x] 7.5 SIGINT integration test under `--progress=json`: assert every still-open phase has a `phase_end(status="cancelled")` line before the orchestrator exits
- [x] 7.6 CLI test matrix for `--progress` values across `optimize`, `run --wait`, `hypothesize`, `tester`, `smoke`, `fetch`

## 8. Lint, docs, rollout

- [x] 8.1 Update `make lint` baselines (mypy strict, ruff) so the new `progress/` module passes without ignores
- [x] 8.2 Add `docs/how-to/read-progress-output.md` showing the four `--progress` modes with sample output, and how to grep JSONL for a specific `path` prefix
- [x] 8.3 Cross-link the new doc from `docs/reference/cli-cookbook.md` next to each long-running command
- [x] 8.4 Flip the default in `cli.py` from `off` to `auto` once stages 1–7 are green; remove the feature gate
- [x] 8.5 Verify CI captures JSONL output cleanly on a non-TTY runner; add a smoke assertion in `.github/workflows/ci.yml` that an `optimize --progress=json` run emits at least one `phase_begin` and one matching `phase_end`
