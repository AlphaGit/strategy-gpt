# Spec: param-optimizer

## Purpose

In-house parameter optimizer that searches over walk-forward folds using grid, random, or Bayesian (TPE) methods. Consumes the strategy's declarative objective spec, delegates evaluation to the Backtest Engine batch API, and emits both an optimized parameter set with aggregated metrics and a natural-language rationale grounded in optimizer output and the knowledge base.
## Requirements
### Requirement: Search method selection

The optimizer SHALL support the following search methods, selectable per optimization run via `experiment.optimize.method`:

- `recursive_grid` (default)
- `grid`
- `random`
- `bayesian` (Tree-structured Parzen Estimator)
- `cma_es` (Covariance Matrix Adaptation Evolution Strategy)
- `differential_evolution`
- `sobol` (Owen-scrambled quasi-random)
- `successive_halving` (multi-fidelity over fold count)
- `lhs_polish` (Latin Hypercube seed + local pattern-search polish)

Each method MUST honor its own method-specific knob sub-block under `experiment.optimize.<method>`; unsupported knob keys MUST be rejected at spec validation time. The optimization manifest MUST record the canonical knob block, the library name + version, and the resolved value of any `auto` knobs.

#### Scenario: Recursive grid sweep with defaults

- **WHEN** the optimizer is invoked with `method: recursive_grid` over a 2-parameter float space and no method-specific overrides
- **THEN** the optimizer applies defaults `resolution: 10`, `top_k: 1`, `depth: 5`, `plateau_epsilon: 0.0001`

#### Scenario: Grid sweep over a small parameter space

- **WHEN** the optimizer is invoked with `method: grid` over a 3-parameter discrete space
- **THEN** every grid point is evaluated and the best per the objective is returned

#### Scenario: Random search budget

- **WHEN** the optimizer is invoked with `method: random, random: {n_iter: N, seed: S}`
- **THEN** exactly N uniformly-sampled candidates are evaluated per fold and the candidate sequence is byte-identical across replays with the same seed

#### Scenario: Bayesian search on an expensive backtest

- **WHEN** the optimizer is invoked with `method: bayesian, bayesian: {budget: N}`
- **THEN** the optimizer runs at most N evaluations using TPE-driven proposals

#### Scenario: CMA-ES on a smooth continuous surface

- **WHEN** the optimizer is invoked with `method: cma_es` over a 3-parameter float space and `cma_es: {popsize: auto, n_generations: 50}`
- **THEN** the optimizer evaluates `popsize × 50` candidates per fold, each generation is dispatched as one packed batch, and the resolved `popsize` is recorded in the manifest

#### Scenario: Differential evolution with Sobol init

- **WHEN** the optimizer is invoked with `method: differential_evolution, differential_evolution: {init: sobol, popsize: auto}`
- **THEN** the first generation's candidates are Sobol points and subsequent generations follow the configured DE strategy; integer parameters declared in the space are integer in every recorded trial

#### Scenario: Sobol quasi-random sampling

- **WHEN** the optimizer is invoked with `method: sobol, sobol: {n_points: 256, scramble: true, owen_seed: 42}`
- **THEN** the 256-point Sobol sequence is evaluated as one packed batch per fold, and the sequence is byte-identical across replays with the same `owen_seed`

#### Scenario: Successive halving on a fold-count fidelity axis

- **WHEN** the optimizer is invoked with `method: successive_halving, successive_halving: {initial_candidates: 64, eta: 3, initial_folds: 2}` against an 8-fold experiment
- **THEN** the optimizer produces rungs with survivor counts following a `1/eta` halving cascade (`64 → 21 → 7 → 2`), each rung's surviving candidates are evaluated on `budget_r = initial_folds × eta^r` folds capped at 8, and candidates eliminated at rung r appear in `trials.parquet` only for the folds they actually ran

#### Scenario: LHS plus Hooke-Jeeves polish

- **WHEN** the optimizer is invoked with `method: lhs_polish, lhs_polish: {lhs_n: 128, top_k_polish: 4, polish: hooke_jeeves}`
- **THEN** 128 LHS points are evaluated per fold, four polish trajectories start from the top-4 LHS points, and Hooke-Jeeves halves its step size whenever a full axis sweep finds no improving move

### Requirement: Multi-metric objective consumption

The optimizer SHALL read the strategy's objective spec and apply the declared tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`). Hard-constraint secondary metrics MUST cause candidate rejection when violated, both during per-fold train search and during final OOS-aggregate scoring.

#### Scenario: Constraint violation rejects a candidate during training

- **WHEN** a candidate's `max_drawdown` exceeds the constraint set in the objective spec on a fold's train slice
- **THEN** the optimizer rejects the candidate within that fold's search regardless of its primary metric value

#### Scenario: Constraint violation rejects a candidate during OOS aggregate

- **WHEN** a fold winner's OOS-aggregate `max_drawdown` exceeds the constraint
- **THEN** the optimizer rejects it as the final candidate even though it won at least one fold

### Requirement: Determinism and seeding

Optimizer runs SHALL be deterministic given the same seed, search method, method-specific knobs, parameter space, fold scheme, library versions, and engine version. Every search method MUST use a seeded RNG whose state and library version are recorded in the optimization manifest.

#### Scenario: Replay an optimization run

- **WHEN** the same experiment-spec is optimized twice with the same seed and pinned library versions
- **THEN** the per-fold winner sequence and the final selection are identical

#### Scenario: CMA-ES determinism across replays

- **WHEN** the same CMA-ES optimization is replayed with the recorded manifest (same seed, popsize, sigma0, bounds, library version)
- **THEN** the generation-by-generation candidate sequence is byte-identical

#### Scenario: Sobol-scramble determinism across replays

- **WHEN** the same Sobol optimization is replayed with the recorded manifest (same `n_points` and `owen_seed`)
- **THEN** the Sobol sequence is byte-identical across replays

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

### Requirement: Benchmark mode

The optimizer SHALL support a `--benchmark` invocation mode that samples a small number of candidates (default 3, configurable via `--sample N`), evaluates them across all folds in one packed batch, measures wall time, and predicts total runtime and ledger footprint for the configured method before any full search begins. The prediction MUST account for the configured method's planned run count, the resolved parallelism, and a worker-pool startup overhead. Predictor formulas MUST cover every supported method, including the special per-rung accounting required for `successive_halving`.

#### Scenario: Benchmark predicts wall time

- **WHEN** the user runs `strategy-gpt optimize --spec experiment.yaml --benchmark` for an experiment that would commission 8,000 backtests at a measured 1.4s per run with 8 effective workers
- **THEN** the runner prints a prediction in the range `[1,200s, 1,600s]` (accounting for startup overhead and a ±20% confidence band) and prompts the user to proceed unless `--yes` is set

#### Scenario: Benchmark accounts for successive halving cascade

- **WHEN** `--benchmark` is invoked with `method: successive_halving, initial_candidates: 64, eta: 3, initial_folds: 2`
- **THEN** the predicted run count is the sum of per-rung evaluations following the survivor cascade, not `initial_candidates × folds`

#### Scenario: Benchmark surfaces method advisory

- **WHEN** `--benchmark` is invoked with `method: cma_es` against a search space containing strictly more integer than float parameters
- **THEN** the report appends a one-line advisory recommending `differential_evolution` as a better fit for mixed-integer spaces

### Requirement: Parallelism auto resolution

When the experiment-spec or CLI declares `parallelism: auto`, the optimizer SHALL resolve it to `max(1, usable_cpu_count - 1)`, honoring OS-level affinity (`sched_getaffinity` on linux) when available. The resolved integer MUST be recorded in the optimization run manifest.

#### Scenario: Auto on a constrained linux host

- **WHEN** the runner is restricted to 6 CPUs via cgroup and `parallelism: auto` is declared
- **THEN** the optimizer resolves parallelism to `5` and records `5` in the manifest

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

