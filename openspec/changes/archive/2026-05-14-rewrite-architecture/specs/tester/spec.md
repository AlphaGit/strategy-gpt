## ADDED Requirements

### Requirement: Hypothesis-to-artifact translation

The Tester SHALL translate a hypothesis into either a typed parameter diff against an existing strategy or a new strategy source emitted by the LLM. The translation MUST preserve the hypothesis identifier so the verdict can be linked back.

#### Scenario: Parameter-change hypothesis

- **WHEN** the hypothesis proposes changing `vol_lo` from 10 to 5 on an existing strategy
- **THEN** the Tester emits a parameter diff and reuses the existing strategy artifact

#### Scenario: Logic-change hypothesis

- **WHEN** the hypothesis proposes adding a new entry filter or replacing an exit rule
- **THEN** the Tester invokes the LLM to emit revised Rust source, builds a new strategy artifact, and links it to the hypothesis id

### Requirement: Compile and lint validation

Before delegating to the engine, the Tester SHALL run the build pipeline (parse, allowed-crate check, `cargo build`) on the candidate artifact. Compile failures or lint rejections MUST cause the Tester to mark the hypothesis as `rejected: build_failed` with the build output captured in the ledger and bypass the engine.

#### Scenario: Emitted source fails to compile

- **WHEN** the LLM-emitted Rust source fails `cargo build`
- **THEN** the Tester records `rejected: build_failed` with the compiler diagnostic and does not invoke the engine

### Requirement: Smoke test on a small slice

After successful build, the Tester SHALL run a smoke backtest on a small data slice (configurable; default a few weeks of bars). A smoke-test failure — panic, no simulated trades emitted when expected, or repeated engine sanity-bound trips (a backtest-validity check, not a live risk control) — MUST cause the Tester to mark the hypothesis as `rejected: smoke_failed` and bypass the full batch.

#### Scenario: Strategy panics on the first day

- **WHEN** the smoke backtest panics on bar 1
- **THEN** the Tester records `rejected: smoke_failed` with the panic message and does not request a full batch

### Requirement: Batch delegation to the engine

After a successful smoke test, the Tester SHALL construct a `BatchSpec` covering the full backtest range plus walk-forward folds and configured stress/sensitivity modes, submit it to the engine, and await results.

#### Scenario: Successful test pipeline

- **WHEN** build, lint, and smoke test all succeed
- **THEN** the Tester submits the full `BatchSpec` and returns the engine's verdict to the Hypothesis Loop

### Requirement: Verdict reporting

The Tester SHALL report a verdict per hypothesis containing: the hypothesis id, build/smoke/full-run statuses, the resulting metrics, whether the hypothesis's falsification criterion was met, and a reference to the run record in the ledger.

#### Scenario: Falsification criterion not met

- **WHEN** the engine returns metrics that do not satisfy the hypothesis's falsification criterion
- **THEN** the Tester reports `verdict: false` with the metric movement that triggered it
