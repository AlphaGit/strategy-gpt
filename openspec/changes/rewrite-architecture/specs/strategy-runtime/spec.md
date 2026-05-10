## ADDED Requirements

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

### Requirement: Allowed-crate whitelist enforced at build time

LLM-emitted strategies SHALL only depend on a maintained whitelist of crates. The build pipeline MUST reject any `Cargo.toml` referencing a crate outside the whitelist. Whitelisted crate versions are NOT pinned; the latest version available within the whitelist is used.

#### Scenario: Strategy attempts to use a non-whitelisted crate

- **WHEN** an emitted strategy declares `tokio` in `Cargo.toml`
- **THEN** the build pipeline rejects the strategy before invoking `cargo build`

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

### Requirement: Build pipeline uses sccache and a local registry mirror

The build pipeline SHALL use `sccache` for incremental compile caching. Crate downloads SHALL be served from a local registry mirror or vendored directory rather than directly from `crates.io`.

#### Scenario: Repeated build of similar strategies

- **WHEN** two strategies share most dependency trees and only differ in their own source
- **THEN** dependency artifacts are reused from `sccache` and only the differing source is recompiled
