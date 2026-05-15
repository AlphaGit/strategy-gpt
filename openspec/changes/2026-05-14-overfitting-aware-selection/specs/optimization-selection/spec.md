# Spec: optimization-selection

## ADDED Requirements

### Requirement: Overfitting-aware final selection

After a parameter-optimization search completes, the system SHALL run an overfitting-aware selection layer over the resulting `trials.parquet` before the `best.json` artifact is written. The layer's computations MUST be a pure function of `trials.parquet`, `manifest.json`, and the selection knobs; running the layer twice with the same inputs MUST produce identical output.

#### Scenario: Selection runs before best.json is published

- **WHEN** an optimization run completes its search and cross-fold OOS validation
- **THEN** the selection layer runs and the resulting `best.json` contains the layer's `pbo`, `deflated_sharpe`, `sensitivity_score`, and `decision` fields

#### Scenario: Selection is a pure function

- **WHEN** the same `trials.parquet`, `manifest.json`, and selection knobs are fed to the layer twice
- **THEN** the two output artifacts compare byte-equal

### Requirement: Probability of Backtest Overfitting via CSCV

The selection layer SHALL compute the Probability of Backtest Overfitting (PBO) for the top-K trials by Combinatorially Symmetric Cross-Validation over the per-fold OOS metric matrix. For S ≤ 16 folds, the layer MUST enumerate every `binom(S, S/2)` split; for S > 16, the layer MUST Monte Carlo sample `max_splits` splits using a seeded RNG whose seed is recorded in the manifest. If PBO exceeds the configured threshold, the layer MUST mark the optimization run as `rejected_pbo` and MUST NOT publish a `best` unless an explicit `--force` override is recorded.

#### Scenario: PBO computed for S=8 folds

- **WHEN** the selection layer runs over a top-K of 50 trials and an 8-fold experiment
- **THEN** the layer enumerates the 70 combinatorial splits and reports PBO as the fraction of splits in which the IS-best trial is in the bottom half of OOS rank

#### Scenario: PBO > threshold rejects the run

- **WHEN** the computed PBO is 0.62 and the threshold is the default 0.5
- **THEN** `best.json` reports `decision: rejected_pbo`, the `best` field is null, and a `would_have_picked` field records the candidate that DSR would have selected

#### Scenario: Force override is recorded

- **WHEN** the user invokes `--force` despite a PBO over threshold
- **THEN** the layer accepts the best per the configured ranking, and the manifest records both the original PBO and the override

### Requirement: Deflated Sharpe Ratio for re-ranking

The selection layer SHALL compute the Deflated Sharpe Ratio (DSR) for each top-K candidate using the Bailey & López de Prado 2014 formulation, accounting for the effective number of distinct parameter sets evaluated, the per-trial Sharpe variance corrected for higher moments (skew, kurt), and the expected maximum Sharpe under the null. When `optimize.robust_objective` is false (default), the layer's final ranking MUST use DSR descending with raw primary score as the tie-breaker.

#### Scenario: DSR ranks top-K candidates

- **WHEN** the selection layer is invoked over a top-K of 50 candidates with `robust_objective: false`
- **THEN** the top-1 candidate in `best.json` is the candidate with the highest DSR; ties are broken by raw primary score

#### Scenario: DSR reported alongside raw Sharpe in every output

- **WHEN** an optimization completes and `best.json` is written
- **THEN** the file records DSR alongside the raw Sharpe (and other primary-metric values) for every top-K candidate, not just the winner

### Requirement: Parameter-sensitivity scoring

The selection layer SHALL compute a parameter-sensitivity (robust) score per top-K candidate as `mean(score over k-NN) - lambda * std(score over k-NN)`, where the neighborhood is the k nearest already-evaluated trials in min-max-normalized parameter space (Euclidean distance over float / int dims; 0/1 distance contribution per categorical mismatch). The candidate's own score participates in the neighborhood mean. The neighborhood is drawn from the full `trials.parquet`, not only the top-K.

#### Scenario: Sensitivity score on a knife-edge surface

- **WHEN** a top-K candidate sits at a single high-score point surrounded by low-score points in normalized parameter space
- **THEN** its robust score is materially lower than its raw score, reflecting local-neighborhood instability

#### Scenario: Robust score is reported but not used for ranking by default

- **WHEN** the selection layer runs with `optimize.robust_objective: false` (default)
- **THEN** `best.json` reports the robust score for every top-K candidate but the ranking still uses DSR

### Requirement: Optional robust-objective selection

The selection layer SHALL support `optimize.robust_objective: true` (also via `--robust-objective` CLI flag) which causes the final ranking to use the parameter-sensitivity (robust) score in place of DSR. This flag MUST NOT affect the search itself — search methods always see the raw objective during their candidate generation and acceptance decisions.

#### Scenario: Robust objective changes the winner

- **WHEN** the same trial set is selected once with `robust_objective: false` and once with `--robust-objective` on a deliberately knife-edge synthetic optimization
- **THEN** the two outputs select different top-1 candidates and both decisions are recorded with their respective ranking criteria

#### Scenario: Robust objective does not affect search

- **WHEN** an optimization runs with `optimize.robust_objective: true`
- **THEN** the per-fold trial records in `trials.parquet` carry the raw objective score, not the robust score; the robust score is computed at selection time only

### Requirement: Post-hoc reselection

The system SHALL support `strategy-gpt optimize reselect <opt_id> [flags...]` which re-runs the selection layer over an existing `trials.parquet` and writes a new `best_<timestamp>.json` adjacent to the original. Reselection MUST NOT overwrite the original `best.json`; the audit trail of selection decisions over the same trial set MUST be preserved.

#### Scenario: Reselect produces an additional artifact

- **WHEN** the user runs `strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7`
- **THEN** a new `best_<timestamp>.json` is created next to the original `best.json`, recording the override threshold and the resulting decision; the original `best.json` is unchanged

#### Scenario: Compare two selections

- **WHEN** the user runs `strategy-gpt optimize compare <opt_id> best.json best_<timestamp>.json`
- **THEN** the tool prints a structured side-by-side diff of the two decisions, including any change of winner, PBO, DSR ranking, and recorded overrides

### Requirement: Citation manifest

Every optimization run's manifest SHALL record citations for the selection methodology applied: the PBO/CSCV paper (Bailey, Borwein, López de Prado, Zhu 2017), the DSR paper (Bailey & López de Prado 2014), and the robust-scoring references (López de Prado 2018, ch. 11–12; Pardo 2008, ch. 9). Citations MUST appear under `selection_methodology` in `manifest.json`.

#### Scenario: Citations recorded for audit

- **WHEN** an optimization run completes with the default selection layer enabled
- **THEN** `manifest.json` includes a `selection_methodology` object listing the three primary citations as strings
