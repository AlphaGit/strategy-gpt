## ADDED Requirements

### Requirement: Typed progress event vocabulary

The system SHALL define a closed set of four progress event kinds, each carrying a dotted-path identifier and a monotonic source clock: `phase_begin`, `phase_progress`, `phase_end`, and `heartbeat`. `phase_begin` MAY carry `total` and `unit`; `phase_progress` MUST carry `current` and MAY carry `total`, `msg`, and a `metrics` mapping of `str → float`; `phase_end` MUST carry a terminal `status` of `ok`, `fail`, `skip`, or `cancelled` plus `wall_secs`; `heartbeat` MUST carry `wall_secs` and `since_last_event_secs`. The vocabulary SHALL be the only contract between event emitters (orchestrator code, Rust workers) and event sinks (renderers, JSONL writer).

#### Scenario: Optimizer emits a typed progress event for each fold trial

- **WHEN** the parameter optimizer completes trial 47 of fold 2 during a `random` search
- **THEN** a `phase_progress` event is published with `path="optimize.fold_2.trial_47"`, `current=47`, `total=200`, and `metrics` containing at least the current trial's primary score

#### Scenario: Worker emits a phase_end with a terminal status

- **WHEN** the Rust worker finishes a per-run backtest successfully
- **THEN** a `phase_end` event is published with `path="worker.batch_<n>.run_<m>"`, `status="ok"`, and `wall_secs` set to the run's wall-clock duration

#### Scenario: Unknown event kinds are rejected

- **WHEN** a deserializer encounters a record whose `kind` is not one of the four defined event kinds
- **THEN** the record is dropped and a structured warning is logged through structlog; no `ProgressEvent` is published

### Requirement: Dotted-path phase identity

Every progress event SHALL carry a `path` that uniquely identifies its phase within the run as a dotted string with monotonic prefixes from the originating component, so consumers can filter, group, and render a phase tree without additional metadata. Orchestrator-emitted paths MUST be prefixed by the command name (`optimize.*`, `hypothesize.*`, `tester.*`, `smoke.*`, `fetch.*`, `run.*`); Rust-worker-emitted paths MUST be prefixed by `worker.*`.

#### Scenario: Path prefix identifies the originating component

- **WHEN** the orchestrator parses a progress event whose `path` starts with `worker.`
- **THEN** the event is rendered under the worker subtree of the active command's phase tree, distinct from orchestrator-emitted phases

#### Scenario: Sibling phases share a parent prefix

- **WHEN** two fold-level phases are open during an optimization (`optimize.fold_1.search` and `optimize.fold_2.search`)
- **THEN** both phases render as children of the `optimize` root node in the phase tree, in the order their `phase_begin` events were observed

### Requirement: Heartbeat for non-hang detection

The orchestrator SHALL ensure that, while any phase is open, at least one progress event is published for that phase every five seconds. When the time since the last event on an open phase exceeds five seconds, the orchestrator MUST synthesize a `heartbeat` event for that phase carrying the elapsed wall time and the seconds since the last event. Heartbeats MUST NOT be emitted for closed phases.

#### Scenario: Idle phase produces a heartbeat

- **WHEN** an open phase produces no `phase_progress` event for six seconds
- **THEN** a single `heartbeat` event is published with `since_last_event_secs >= 5` and `wall_secs` equal to elapsed time since the phase's `phase_begin`

#### Scenario: Heartbeat is suppressed when the phase ends in the same window

- **WHEN** an open phase emits a `phase_end` event in the same one-second scan tick that would otherwise have produced a heartbeat
- **THEN** no `heartbeat` event is published for that phase in that tick

### Requirement: Source-side event coalescing

Emitters in hot loops (per-bar inside a backtest, per-trial inside a fold) SHALL coalesce repeated `phase_progress` events for the same `path` into at most one event per 250 milliseconds, preserving the highest observed `current` value within the window. `phase_begin` and `phase_end` events MUST NOT be coalesced or dropped. `heartbeat` events MUST NOT be coalesced.

#### Scenario: Per-bar ticks are throttled

- **WHEN** a backtest emits per-bar progress events at 10 kHz for `path="worker.batch_3.bar_loop"`
- **THEN** the observed `phase_progress` event rate for that path on the bus does not exceed five events per second, and the `current` value of the most recent emitted event reflects the highest bar index seen so far

#### Scenario: Phase boundaries are never dropped

- **WHEN** a phase begins and ends within the 250-millisecond coalescing window
- **THEN** both the `phase_begin` and `phase_end` events for that phase are published

### Requirement: Rust-worker progress bridge over stderr

Rust worker processes SHALL emit progress events as `tracing` records with `target = "progress"` on stderr, using the same JSON formatter installed by `crates/engine/src/logging.rs`. The Python orchestrator SHALL read each worker's stderr line by line, parse each JSON record, route records with `target == "progress"` into the `ProgressBus` as `ProgressEvent`s, and forward all other records unchanged to the existing structlog stream.

#### Scenario: Worker progress records are routed to the bus

- **WHEN** the Rust worker writes a stderr line whose JSON envelope has `target == "progress"` and a `path` field
- **THEN** the orchestrator deserializes it into a `ProgressEvent` and publishes it on the `ProgressBus` without writing the original line to the structlog stream

#### Scenario: Non-progress worker records flow through structlog

- **WHEN** the Rust worker writes a stderr line whose JSON envelope has `target != "progress"`
- **THEN** the orchestrator forwards the record unchanged to the structlog stream and does not publish it on the `ProgressBus`

#### Scenario: Malformed progress record does not silence logging

- **WHEN** a stderr line claims `target == "progress"` but cannot be parsed as a `ProgressEvent`
- **THEN** the orchestrator drops it from the progress stream, logs a structured warning, and continues processing subsequent lines from the same worker

### Requirement: CLI progress flag with four modes

Every long-running CLI command SHALL accept a `--progress` option taking exactly one of `auto`, `plain`, `json`, or `off`, with `auto` as the default. `auto` MUST install the rich `Live` renderer when `sys.stderr.isatty()` is true and the JSONL sink otherwise. `plain` MUST install a text sink that emits one human-readable line per `phase_begin` and `phase_end`, plus heartbeats no more often than once every thirty seconds, with no ANSI escapes. `json` MUST install the JSONL sink and emit every event verbatim. `off` MUST install no progress sink while leaving structlog and tracing unaffected.

#### Scenario: Non-TTY default selects JSONL

- **WHEN** `strategy-gpt optimize --spec ...` is run with `--progress=auto` and stderr is a pipe
- **THEN** the orchestrator installs the JSONL sink, writes one JSON line per progress event to stderr, and does not write any ANSI escape sequences

#### Scenario: TTY default selects the rich renderer

- **WHEN** the same command runs with `--progress=auto` and stderr is a terminal
- **THEN** the orchestrator installs the rich `Live` renderer with a phase tree on stderr, refreshing at no more than ten frames per second

#### Scenario: Off disables the progress sink only

- **WHEN** the command runs with `--progress=off`
- **THEN** no progress events are rendered to any sink, structlog continues to emit JSON records to stderr at the configured level, and Rust `tracing` records continue to flow as before

### Requirement: TTY renderer uses rich Live and writes to stderr

The TTY renderer SHALL use `rich.live.Live` driving a `rich.tree.Tree` of phase nodes, with each open phase having a known `total` rendered via a `rich.progress.Progress` task and each open phase without a `total` rendered via a `rich.spinner.Spinner` with elapsed seconds. The renderer's `Console` MUST be constructed with `stderr=True` so stdout remains reserved for command results. Renderer refresh rate MUST NOT exceed ten frames per second.

#### Scenario: Stdout remains free of progress output

- **WHEN** an interactive `optimize` run writes its terminal JSON result to stdout while the renderer is active
- **THEN** stdout contains only the JSON result and no ANSI escapes, control characters, or progress lines

#### Scenario: Phases without a known total render as spinners

- **WHEN** a CMA-ES search phase begins with `total=None`
- **THEN** the renderer displays a spinner with elapsed seconds for that phase rather than a progress bar, and updates the spinner state on each `phase_progress` event subject to the refresh-rate cap

### Requirement: Clean cancellation flushes the renderer

On receipt of SIGINT (or any orchestrator-level cancellation), the orchestrator SHALL flush the `ProgressBus`, synthesize a `phase_end` event with `status="cancelled"` for every still-open phase, allow the active sink to render its final state, and only then propagate the interrupt to its existing teardown path that signals workers.

#### Scenario: SIGINT during an optimization

- **WHEN** the user presses Ctrl-C while `optimize` is running with `--progress=auto` on a TTY
- **THEN** every open phase receives a `phase_end(status="cancelled")` event, the rich renderer paints its final state showing those terminations, and the renderer's `Live` context exits before the worker subprocess is signaled

#### Scenario: SIGINT during JSONL mode

- **WHEN** the user presses Ctrl-C while `--progress=json` is active
- **THEN** a `phase_end(status="cancelled")` JSON line is written for every still-open phase before the orchestrator's existing teardown signals workers

### Requirement: Progress events excluded from the experiment ledger

Progress events SHALL NOT be written to the experiment ledger nor to its parquet sidecars. The experiment ledger's reproducibility contract remains based solely on inputs (artifact hash, dataset manifest, parameters, modes, seed, runner version) and recorded results.

#### Scenario: Replay byte-identity preserved

- **WHEN** an optimization is run twice with the same inputs, once with `--progress=auto` on a TTY and once with `--progress=off`
- **THEN** the resulting ledger rows and parquet sidecars are byte-identical between the two runs

#### Scenario: Ledger contains no progress fields

- **WHEN** any ledger row written by `optimization_runner` is inspected after a run that used `--progress=json`
- **THEN** no field in that row carries any `ProgressEvent` payload, heartbeat record, or path string

### Requirement: Hypothesis-loop surfaces LLM reasoning previews

The hypothesis loop SHALL emit a `phase_progress` event on every LangGraph node transition under the `hypothesize.*` path. During the `hypothesize.generate` phase, each new reasoning summary returned by the LLM MUST be published as a `phase_progress` event whose `msg` field carries the most recent summary truncated to at most 120 characters. Full reasoning records continue to be written through structlog without truncation.

#### Scenario: Node transition emits a progress event

- **WHEN** the hypothesis loop transitions from the `diagnose` node to the `kb_query` node
- **THEN** a `phase_end` event for `hypothesize.diagnose` and a `phase_begin` event for `hypothesize.kb_query` are published in that order

#### Scenario: Reasoning summary appears in msg field

- **WHEN** the LLM returns an interim reasoning summary during `hypothesize.generate`
- **THEN** a `phase_progress` event for `hypothesize.generate` is published with `msg` set to that summary truncated to at most 120 characters, and the full untruncated summary is written to the structlog stream

### Requirement: Instrumented long-running commands

The following commands SHALL emit progress events covering their full lifecycle: `optimize`, `run --wait`, `hypothesize`, `tester` (lint, build, smoke, full batch sub-phases), `smoke`, and `fetch` (per-provider sub-phases when fetching). Each command MUST emit at least one `phase_begin` and one `phase_end` for its top-level phase, plus interior `phase_progress` events for every loop with a known bound greater than one.

#### Scenario: Optimize lifecycle is fully bracketed

- **WHEN** `strategy-gpt optimize --spec ...` runs to completion with `--progress=json`
- **THEN** the JSONL output starts with `phase_begin path="optimize"` and ends with `phase_end path="optimize" status="ok"`, and every fold's search and OOS cross-validation sub-phases each appear as a matched `phase_begin`/`phase_end` pair

#### Scenario: Fetch surfaces provider sub-phases

- **WHEN** `strategy-gpt fetch --provider yfinance ...` runs against a cold cache
- **THEN** `phase_begin path="fetch.yfinance.download"` and a matching `phase_end` appear in the event stream, with at least one `phase_progress` event reflecting bytes or rows downloaded

### Requirement: Score events surface their underlying metrics

Whenever a progress event reports a score (orchestrator-side `phase_progress` events emitted by the parameter optimizer, the rich live renderer's metric inline display, and the legacy `StderrProgressRenderer`'s accepted-trial / cross-validation / final-pick lines), the same event or output line MUST also surface the metric values that produced that score. The intent is that a human reading the progress stream sees *what* the score reflects — primary metric plus guard metrics, OOS aggregates, etc. — not just its numeric value.

Numeric values that pass through ProgressBus's `metrics` mapping continue to be `dict[str, float]`; non-numeric or boolean values (e.g. counts, flags) MUST be filtered out at the emitter, not at the sink.

#### Scenario: Trial-tick metrics include the score components

- **WHEN** the optimizer emits a `phase_progress` event for an accepted trial via the orchestrator's progress tee
- **THEN** the event's `metrics` mapping contains `score`, `best`, and every numeric entry from the trial's `metrics` (e.g. `sharpe`, `sortino`, `max_drawdown`), so a downstream JSONL consumer can join score to its drivers without consulting the ledger

#### Scenario: Phase-end summary surfaces winning metrics

- **WHEN** the `StderrProgressRenderer` flushes a phase that had at least one accepted trial
- **THEN** the rendered summary line carries the best primary metric and the score, AND a follow-up line carries every numeric metric of the winning trial

### Requirement: Number formatting and column alignment

All numeric values rendered to a human-facing progress sink (TTY live renderer, plain-text sink, `StderrProgressRenderer`) MUST follow a consistent format:

- Decimals: fractional values render with at most 4 places after the decimal point. No scientific notation, no `.4g` rounding to fewer digits than the human eye expects.
- Integer-valued numbers: trade counts, bar counts, volumes, and any other integer-valued metric (including floats whose value equals their integer truncation, e.g. `14.0`) MUST render without a decimal point or trailing zeros — `14`, not `14.0000`.
- Large quantities (integer counts, trial totals, bar counts, monetary amounts): rendered with a thousands separator using the user's locale-independent default (`","`).
- Whenever multiple values are shown across rows (cross-validation winners table, OOS aggregate metrics list, per-fold summary), values in the same column MUST be left-/right-padded so the columns visually align — score columns right-align, label columns left-align.

JSON / JSONL output is exempt: machine-readable streams use the JSON number representation unchanged.

#### Scenario: Score values use 4 decimals and thousands separators

- **WHEN** the renderer emits a score of `1234.56789012` for a trial
- **THEN** the rendered text is `1,234.5679` — four decimals, comma thousands separator — and not `1234.56789012`, `1234.57`, or scientific notation

#### Scenario: Integer-valued metrics render without decimals

- **WHEN** the renderer emits an integer-valued metric (e.g. `n_trades=14.0`, `avg_trade_length_bars=9_557_640.0`, a bar count, a trial-total volume)
- **THEN** the rendered text is `14`, `9,557,640` — no decimal point, no trailing `.0000` — even when the underlying value is a `float`

#### Scenario: Cross-validation table aligns columns

- **WHEN** the optimization renderer prints two or more `cross_validation` winner rows
- **THEN** the winner label column is left-padded to the widest label, the primary-metric column and the score column are right-padded to their widest value, so the columns line up visually regardless of the number of fold winners
