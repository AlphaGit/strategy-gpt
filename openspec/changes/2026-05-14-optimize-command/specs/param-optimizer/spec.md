# Spec: param-optimizer

## MODIFIED Requirements

### Requirement: Per-fold optimization with rolling folds

The Parameter Optimizer SHALL run a search per fold of the experiment's fold scheme. For each fold, the optimizer MUST execute the configured search method against the fold's *train* slice and select a fold winner by the objective score on that train slice. After all folds have produced winners, the optimizer MUST cross-validate every fold winner across every fold's *OOS* slice and select a single final parameter set by best OOS-aggregate score (`aggregator: mean` for v1; constraints and soft-secondaries from the objective spec apply unchanged to the aggregated metrics).

#### Scenario: Per-fold train search

- **WHEN** the optimizer runs on an experiment with `folds: {count: 8, scheme: rolling}` and a search space
- **THEN** the engine receives eight packed batches (one per fold) each containing the round's candidate set evaluated against that fold's train slice

#### Scenario: Cross-fold OOS validation

- **WHEN** eight fold winners have been selected
- **THEN** the optimizer evaluates all eight winners across all eight OOS slices in one additional packed batch and selects the candidate whose mean OOS score is highest

#### Scenario: Tie-break by stability

- **WHEN** two fold winners tie on OOS-aggregate score within `1e-6`
- **THEN** the optimizer selects the candidate with the lower variance of per-fold OOS scores

### Requirement: Search method selection

The optimizer SHALL support `recursive_grid` (default), `grid`, `random`, and `bayesian` (TPE) search methods, selectable per optimization run via `experiment.optimize.method`. Each method MUST honor its own method-specific knob sub-block; unsupported knob keys MUST be rejected at spec validation time.

#### Scenario: Recursive grid sweep with defaults

- **WHEN** the optimizer is invoked with `method: recursive_grid` over a 2-parameter float space and no method-specific overrides
- **THEN** the optimizer applies defaults `resolution: 10`, `top_k: 1`, `depth: 5`, `plateau_epsilon: 0.0001`

#### Scenario: Grid sweep over a small parameter space

- **WHEN** the optimizer is invoked with `method=grid` over a 3-parameter discrete space
- **THEN** every grid point is evaluated and the best per the objective is returned

#### Scenario: Bayesian search on an expensive backtest

- **WHEN** the optimizer is invoked with `method=bayesian, budget=N evaluations`
- **THEN** the optimizer runs at most N evaluations using TPE-driven proposals

### Requirement: Recursive grid with plateau stop

`recursive_grid` SHALL iterate as: at each round, evaluate a uniform grid of `resolution^D` points within the current box (where D is the search-space dimensionality); select the top `top_k` cells by candidate score; shrink the box to the union of selected cells; continue until either `depth` rounds have elapsed *or* every dimension's current box width is below `plateau_epsilon × original_dimension_range`. Stop only when *all* dimensions have converged (per-dim AND, not OR).

#### Scenario: All-dim convergence stops early

- **WHEN** at the end of round 3, every dimension's current box is narrower than `plateau_epsilon` times the original range
- **THEN** the optimizer terminates after round 3 even though `depth: 5` was configured

#### Scenario: Partial convergence does not stop

- **WHEN** at the end of round 3, only the first dimension has converged
- **THEN** the optimizer continues to round 4

#### Scenario: Integer dimension freezes on collapse

- **WHEN** during recursion an integer dimension's current cell collapses to a single integer value
- **THEN** the optimizer freezes that dimension and continues refining the others

### Requirement: Multi-metric objective consumption

The optimizer SHALL read the strategy's objective spec and apply the declared tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`). Hard-constraint secondary metrics MUST cause candidate rejection when violated, both during per-fold train search and during final OOS-aggregate scoring.

#### Scenario: Constraint violation rejects a candidate during training

- **WHEN** a candidate's `max_drawdown` exceeds the constraint set in the objective spec on a fold's train slice
- **THEN** the optimizer rejects the candidate within that fold's search regardless of its primary metric value

#### Scenario: Constraint violation rejects a candidate during OOS aggregate

- **WHEN** a fold winner's OOS-aggregate `max_drawdown` exceeds the constraint
- **THEN** the optimizer rejects it as the final candidate even though it won at least one fold

### Requirement: Determinism and seeding

Optimizer runs SHALL be deterministic given the same seed, search method, parameter space, fold scheme, and engine version. Random, Bayesian, and `recursive_grid` (when `top_k` ties occur) methods MUST use seeded RNGs whose state is recorded in the optimization manifest.

#### Scenario: Replay an optimization run

- **WHEN** the same experiment-spec is optimized twice with the same seed and engine version
- **THEN** the per-fold winner sequence and the final selection are identical

## ADDED Requirements

### Requirement: Benchmark mode

The optimizer SHALL support a `--benchmark` invocation mode that samples a small number of candidates (default 3, configurable via `--sample N`), evaluates them across all folds in one packed batch, measures wall time, and predicts total runtime and ledger footprint for the configured method before any full search begins. The prediction MUST account for the configured method's planned run count, the resolved parallelism, and a worker-pool startup overhead.

#### Scenario: Benchmark predicts wall time

- **WHEN** the user runs `strategy-gpt optimize --spec experiment.yaml --benchmark` for an experiment that would commission 8,000 backtests at a measured 1.4s per run with 8 effective workers
- **THEN** the runner prints a prediction in the range `[1,200s, 1,600s]` (accounting for startup overhead and a ±20% confidence band) and prompts the user to proceed unless `--yes` is set

### Requirement: Parallelism auto resolution

When the experiment-spec or CLI declares `parallelism: auto`, the optimizer SHALL resolve it to `max(1, usable_cpu_count - 1)`, honoring OS-level affinity (`sched_getaffinity` on linux) when available. The resolved integer MUST be recorded in the optimization run manifest.

#### Scenario: Auto on a constrained linux host

- **WHEN** the runner is restricted to 6 CPUs via cgroup and `parallelism: auto` is declared
- **THEN** the optimizer resolves parallelism to `5` and records `5` in the manifest

### Requirement: Optimization persistence and replay

For every optimization run, the optimizer SHALL create a per-run directory under `ledger/optimizations/<opt_id>/` containing: a `manifest.json` (experiment-spec hash, artifact hash, dataset hash, resolved parallelism, runner version, seed, status, timestamps), a `trials.parquet` table with one row per backtest the optimizer commissioned (trial_id, round, phase, fold_index, params, seed, metrics, score, accepted, reject_reason, wall_secs), and a `best.json` pointer to the winning trial. An `optimizations.sqlite` index at the ledger root SHALL record one row per optimization run for cross-run discovery. Per-trial full `BacktestResult` payloads SHALL NOT be persisted; the optimizer MUST instead support `strategy-gpt optimize replay <opt_id> --trial <trial_id>` which reconstructs and re-submits the single-run BatchSpec from the trial row + manifest, producing a byte-identical `BacktestResult`.

#### Scenario: Per-trial replay is byte-identical

- **WHEN** the user runs `strategy-gpt optimize replay <opt_id> --trial 4271`
- **THEN** the engine returns a `BacktestResult` that matches what the original optimization run produced for that trial bit-for-bit

#### Scenario: Footprint scales linearly with trial count

- **WHEN** an optimization run commissions 1,000,000 trials
- **THEN** `trials.parquet` is on the order of hundreds of megabytes and contains no full `BacktestResult` payloads

## REMOVED Requirements

### Requirement: Optimized output and rationale

**Reason**: The "natural-language rationale grounded in optimizer output and the knowledge base" remains a desired feature but is deferred to a follow-up change. The `optimize` command as introduced here emits structured outputs only (best params, fold winners, aggregated metrics, ledger pointer). Rationale generation is gated on KB integration design that has not yet stabilized.

**Migration**: When the follow-up rationale change lands, it will re-introduce this requirement (renamed) under `param-optimizer` and wire it to the hypothesis-loop / KB retrieval surfaces. The structured outputs produced by this change are forward-compatible.
