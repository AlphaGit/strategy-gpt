# Spec: strategy-runtime

## Purpose

Defines the sealed `Strategy` trait, the `Context` capability handle, the allowed-crate build pipeline, content-addressed artifact storage, semver-based runner versioning, and the cdylib + C-ABI load mechanism that worker processes use to instantiate compiled strategies. Strategy code is the only place LLM-emitted code runs as native code; worker process isolation is the load-bearing safety boundary.

## Requirements

### Requirement: Sealed `Strategy` trait as the only strategy entry point

The Strategy Runtime SHALL define a sealed `Strategy` trait with the following lifecycle methods: `metadata`, `on_init`, `on_bar`, `on_fill`, `on_end`. Strategies interact with the engine exclusively through this trait and a `Context` capability handle passed to each method. The trait MUST be sealed so external implementations cannot bypass the engine API.

#### Scenario: Strategy implements the full lifecycle

- **WHEN** a strategy artifact is loaded
- **THEN** the engine invokes `on_init` once before any bars, `on_bar` for each bar in chronological order, `on_fill` whenever a submitted order fills, and `on_end` after the last bar

### Requirement: `Context` capability handle

The `Context` SHALL expose only engine-mediated capabilities to strategy code, scoped to what a strategy needs to make backtest decisions:

- `submit_order(symbol, side, size, limit_price, stop_price, reason)` — submit a trade intent. There is no `cancel_order`: this is a research platform with no live order book; a strategy that wants to reverse course submits a closing intent on the next bar.
- `get_position(symbol)` — accounting view of the current position (size, avg_price). Realized and unrealized P&L are produced by the engine post-hoc as part of metrics output and are NOT exposed to running strategies.
- `log_signal(name, value, fired, suppressed_by)`
- `log_decision(event, details)`
- `read_indicator(name)` — engine-provided computed indicators
- Engine-managed state get/set for reproducibility

`Context` MUST NOT expose direct filesystem, network, or arbitrary syscall access. `Context` MUST NOT expose any live-trading concept (order cancellation, real-time fills, broker handles).

#### Scenario: Strategy logs a suppressed signal

- **WHEN** a strategy evaluates a signal that fires but is then blocked by a strategy-internal filter
- **THEN** the strategy calls `ctx.log_signal(name, value, fired=false, suppressed_by=Some("trend_filter"))` and the entry appears in the run's `signals` output

#### Scenario: Strategy reverses position without cancellation

- **WHEN** a strategy decides to flip from long to short
- **THEN** it submits a closing intent (or a sufficiently large opposing intent) on the next bar; there is no API to cancel a previously submitted intent

#### Scenario: No live-trading concept reachable

- **WHEN** strategy code attempts to query pending orders, broker handles, account balances, or any real-time market state
- **THEN** no such API exists on `Context`; the strategy fails to compile against the runtime surface

### Requirement: Allowed-crate whitelist documented and lint-enforced

LLM-emitted strategies SHALL only depend on a maintained whitelist of crates. The whitelist is documented in `crates/build-pipeline/whitelist.toml` and included verbatim in the LLM strategy-generation prompt. The build pipeline's source/manifest linter MUST reject any strategy whose `Cargo.toml` declares a non-whitelisted dependency. Whitelisted crate versions are NOT pinned; the latest version available within the whitelist is used. No private cargo registry mirror is maintained; the documented-whitelist + linter combination is the dependency-surface guard, and worker-process isolation is the load-bearing safety boundary.

#### Scenario: Strategy attempts to use a non-whitelisted crate

- **WHEN** an emitted strategy declares `tokio` in `Cargo.toml`
- **THEN** the manifest linter rejects the strategy before invoking `cargo build`

#### Scenario: Whitelisted crate updates upstream

- **WHEN** an upstream whitelisted crate publishes a new version
- **THEN** the next strategy build picks up the new version automatically without configuration changes

### Requirement: Content-addressed strategy artifacts

Each compiled strategy SHALL be stored as an artifact keyed by `hash(source)`. The build pipeline MUST reuse an existing artifact when the same source is encountered again. Artifacts MUST record the runner version they were built against.

#### Scenario: Reusing a previously compiled strategy

- **WHEN** the LLM emits source byte-identical to a previously built strategy
- **THEN** the build pipeline returns the existing artifact without recompiling

### Requirement: Semver runner versioning, no backward compatibility

The runner SHALL follow semantic versioning. The runtime MUST NOT carry multiple ABI versions in parallel. When the runner increments to a new major version, existing strategy artifacts at the old version are detected, flagged, and migrated by re-emitting source through the LLM and rebuilding against the new ABI.

#### Scenario: Runner major version bump

- **WHEN** the runner moves from `1.x` to `2.0` and a strategy artifact at runner version `1.x` is requested
- **THEN** the system flags the artifact, regenerates source via the LLM under the new ABI, rebuilds, and stores a new artifact at runner version `2.0`

#### Scenario: Single ABI in the runtime

- **WHEN** the runtime loads any strategy artifact
- **THEN** all loaded artifacts share the same runner ABI version; mixed-version loading is not supported

### Requirement: Build pipeline uses sccache for incremental compilation

The build pipeline SHALL use `sccache` for incremental compile caching when available. Strategies depend on whitelisted crates fetched from `crates.io` directly; the documented-whitelist + manifest linter combination is the dependency-surface guard (see "Allowed-crate whitelist" above).

#### Scenario: Repeated build of similar strategies

- **WHEN** two strategies share most dependency trees and only differ in their own source
- **THEN** dependency artifacts are reused from `sccache` and only the differing source is recompiled

### Requirement: Strategies load via cdylib + C-ABI registration macro

Compiled strategy artifacts SHALL be `cdylib`s implementing `engine_rt::Strategy`. A registration macro exported from `engine-rt` (`strategy_entry!`) emits a `#[no_mangle] extern "C"` entry point that returns a boxed strategy instance; the engine worker `libloading`-loads the artifact, resolves the macro-emitted symbol, and obtains the `Box<dyn Strategy>` handle. Strategy authors do NOT write `unsafe` or `extern "C"` themselves — the macro generates them on the author's behalf and the trusted `engine-rt` source is exempt from the strategy linter.

#### Scenario: Worker loads a freshly built strategy artifact

- **WHEN** the engine worker receives a compiled strategy artifact path
- **THEN** it opens the cdylib via `libloading`, resolves the registration symbol emitted by `strategy_entry!`, and instantiates the strategy through the C-ABI handle without recompiling the worker binary

#### Scenario: Strategy author never writes unsafe or extern "C"

- **WHEN** a strategy author writes `strategy_entry!(MyStrategy);` after implementing `Strategy` for `MyStrategy`
- **THEN** the source linter sees only the macro invocation and accepts it; the expanded `#[no_mangle] extern "C"` registration symbol is generated by the trusted `engine-rt` macro at compile time
