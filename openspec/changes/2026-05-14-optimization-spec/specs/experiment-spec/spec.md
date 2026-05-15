# Spec: experiment-spec

## ADDED Requirements

### Requirement: Optional optimize block

An experiment-spec MAY include an `optimize` block declaring a parameter search over the experiment's run template. The block SHALL declare: `method` (one of `recursive_grid`, `grid`, `random`, `bayesian`), `seed`, `aggregator` (currently `mean`), a `space` map of per-parameter shapes (`{type: float|int|choice, ...}`), method-specific knob sub-blocks, and a `persist` sub-block (`root`, `name`). When `optimize` is present, the spec MUST also include a `folds` block.

#### Scenario: Spec with optimize requires folds

- **WHEN** an experiment-spec declares an `optimize` block but no `folds` block
- **THEN** validation fails with a structured error naming the missing block

#### Scenario: Search space disjoint from fixed params

- **WHEN** an experiment-spec declares both `runs[0].params.vol_lo: 0.3` and `optimize.space.vol_lo: {type: float, low: 0.1, high: 0.6}`
- **THEN** validation fails with a structured error reporting the conflicting key

### Requirement: Optional folds block

An experiment-spec MAY include a `folds` block declaring how to split the experiment's time slice into training and out-of-sample segments. The block SHALL declare: `count` (≥ 2), `scheme` (`rolling` or `anchored`), `gap` (≥ 0 bars between train end and OOS start), and optional `warmup_bars` (≥ 0). When present, the runner MUST derive `count` train/OOS pairs from the experiment's base slice according to the scheme.

#### Scenario: Rolling fold derivation

- **WHEN** an experiment-spec declares `folds: {count: 4, scheme: rolling, gap: 0}` over a base slice spanning eight years
- **THEN** the runner produces four equal-width sliding (train, OOS) pairs that together tile the slice with no overlap beyond the sliding-window structure

#### Scenario: Anchored fold derivation

- **WHEN** an experiment-spec declares `folds: {count: 4, scheme: anchored, gap: 0}` over the same slice
- **THEN** the runner produces four (train, OOS) pairs whose train segments share a common start and grow over time, while OOS segments slide

#### Scenario: Folds block uses absolute slice

- **WHEN** an experiment-spec has no top-level slice but the run template declares one
- **THEN** the runner derives folds against the run-template slice; if neither is present, validation fails
