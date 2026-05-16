# Spec: param-optimizer

## MODIFIED Requirements

### Requirement: Optimization persistence and replay

For every optimization run, the optimizer SHALL create a per-run directory under `ledger/optimizations/<opt_id>/` containing: a `manifest.json` (experiment-spec hash, artifact hash, dataset hash, resolved parallelism, runner version, library versions, seed, status, timestamps, **`selection_methodology` citations**), a `trials.parquet` table with one row per backtest the optimizer commissioned, and a `best.json` pointer to the winning trial enriched with selection-layer fields (`pbo`, `deflated_sharpe` per top-K, `sensitivity_score` per top-K, `decision` ∈ `{accepted, rejected_pbo, rejected_constraint}`, optional `would_have_picked` when `decision != accepted`). An `optimizations.sqlite` index at the ledger root SHALL record one row per optimization run for cross-run discovery, including the selection `decision` for quick filtering. Per-trial full `BacktestResult` payloads SHALL NOT be persisted; the optimizer MUST instead support `strategy-gpt optimize replay <opt_id> --trial <trial_id>` which reconstructs and re-submits the single-run BatchSpec from the trial row + manifest, producing a byte-identical `BacktestResult`.

#### Scenario: Per-trial replay is byte-identical

- **WHEN** the user runs `strategy-gpt optimize replay <opt_id> --trial 4271`
- **THEN** the engine returns a `BacktestResult` that matches what the original optimization run produced for that trial bit-for-bit

#### Scenario: best.json carries selection fields

- **WHEN** an optimization completes with the default selection layer enabled
- **THEN** `best.json` includes `pbo`, `deflated_sharpe` (top-K array), `sensitivity_score` (top-K array), `decision`, and `selection_methodology` citations; the `optimizations.sqlite` index row records `decision` for the run

#### Scenario: Footprint scales linearly with trial count

- **WHEN** an optimization run commissions 1,000,000 trials
- **THEN** `trials.parquet` is on the order of hundreds of megabytes and contains no full `BacktestResult` payloads

## ADDED Requirements

### Requirement: Selection-layer invocation

The optimizer SHALL invoke the `optimization-selection` capability after the search and cross-fold OOS validation complete, and before `best.json` is written. The optimizer MUST NOT publish a `best` candidate without first running the selection layer; the layer's `decision` field determines whether `best` is populated or null (with a `would_have_picked` reference for transparency).

#### Scenario: Optimizer always invokes selection

- **WHEN** an optimization completes its search
- **THEN** the selection layer runs over the resulting `trials.parquet` before `best.json` is written, and the file's `decision` field is populated

#### Scenario: Rejected run carries would_have_picked

- **WHEN** the selection layer rejects the run with `decision: rejected_pbo`
- **THEN** `best.json` has `best: null` and `would_have_picked` referencing the trial that the configured ranking would have picked in the absence of the rejection

### Requirement: Selection-layer CLI flags

The `strategy-gpt optimize` command SHALL accept `--robust-objective` (overrides `optimize.robust_objective` to true), `--pbo-threshold T` (overrides the PBO rejection threshold; T in `[0, 1]`), and `--force` (proceeds despite a `rejected_pbo` decision; the override and the original PBO value MUST both be recorded in the manifest). A subcommand `strategy-gpt optimize reselect <opt_id> [flags...]` SHALL re-run the selection layer against an existing optimization's artifacts and write a new `best_<timestamp>.json` adjacent to the original without overwriting it.

#### Scenario: Force override is recorded

- **WHEN** the user invokes `strategy-gpt optimize --spec experiment.yaml --force` and the selection layer would otherwise have rejected the run with `decision: rejected_pbo`
- **THEN** the run proceeds to publish `best`, and `manifest.json` records both the original PBO and the explicit force override

#### Scenario: Reselect preserves the original best.json

- **WHEN** the user runs `strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7`
- **THEN** a `best_<timestamp>.json` is created next to the original `best.json`, the original is unchanged, and the manifest's history records the reselection event
