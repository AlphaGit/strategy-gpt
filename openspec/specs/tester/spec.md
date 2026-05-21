# Spec: tester

## Purpose

Translates a hypothesis into either a typed parameter diff against an existing strategy or new LLM-emitted Rust source, validates compilation and a smoke backtest, then delegates the full backtest batch (including walk-forward folds and configured stress/sensitivity modes) to the engine. Reports a verdict per hypothesis with build, smoke, and full-run statuses and a reference to the ledger record.

## Requirements

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

### Requirement: Attempt-with-optimize surface for logic-change candidates

The Tester SHALL expose an `attempt_with_optimize(artifact, param_intent, falsification, folds, method, trials)` surface that builds the candidate artifact, runs a mini-optimization pass over the LLM-declared `param_intent` bounds across the supplied folds with the supplied search `method` and `trials` budget, and returns a structured result containing the per-fold best scores, the aggregate score, the best parameter assignment, side-effect flags relative to the baseline, and the comparative falsification verdict against the baseline-best result.

#### Scenario: Returns per-fold spread and side-effect flags

- **WHEN** a Hypothesis Loop candidate is submitted via `attempt_with_optimize` with five folds and the sobol method
- **THEN** the returned result includes one best score per fold, an aggregate score, the best parameters, a list of `side_effect_flags` (e.g., `trade_count_2x`, `holding_period_halved`) computed against the baseline, and a `falsification_check` block with the primary claim verdict and per-guard verdicts

#### Scenario: Removed parameters are absent from the search space

- **WHEN** `param_intent.removed` lists `trail_stop_atr_mult`
- **THEN** the mini-optimize search never instantiates `trail_stop_atr_mult` and `best_params` does not contain that key

### Requirement: Expanded reject-reason taxonomy

The Tester's reject-reason taxonomy SHALL include, in addition to the existing `build_failed` and `smoke_failed`: `reject_format` (LLM emission failed the markdown parse contract), `reject_schema` (the `param_intent` references parameters absent from the compiled artifact's declared schema, or violates declared bounds), `reject_noise` (the candidate's score did not clear the variance-aware floor against baseline-best), `reject_variance` (the candidate's per-fold CV exceeded the configured threshold), `reject_verdict` (verdict-critique rejected the candidate after a successful mechanical gate), and `reject_deps` (the candidate's Cargo.toml declared a crate outside the allowed-crate whitelist). Each rejection MUST persist a structured rationale that identifies which check fired and includes the relevant evidence (e.g., `rustc` diagnostics for `reject_build`, `σ_combined` and `delta` for `reject_noise`).

#### Scenario: Schema mismatch rejection

- **WHEN** the candidate's compiled artifact declares parameters `{vol_lo, vol_hi}` but its `param_intent.added` references `hedge_ratio`
- **THEN** the Tester records `reject_schema` with a rationale listing the missing parameter

#### Scenario: Noise rejection records the gap

- **WHEN** the candidate's `(score - baseline_score)` is positive but smaller than `k · σ_combined`
- **THEN** the Tester records `reject_noise` with a rationale that includes `score`, `baseline_score`, `σ_combined`, and `k`
