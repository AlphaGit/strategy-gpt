# Spec: param-optimizer

## MODIFIED Requirements

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
