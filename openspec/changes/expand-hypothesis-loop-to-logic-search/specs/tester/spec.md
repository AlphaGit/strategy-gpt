## ADDED Requirements

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
