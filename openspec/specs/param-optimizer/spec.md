# Spec: param-optimizer

## Purpose

In-house parameter optimizer that searches over walk-forward folds using grid, random, or Bayesian (TPE) methods. Consumes the strategy's declarative objective spec, delegates evaluation to the Backtest Engine batch API, and emits both an optimized parameter set with aggregated metrics and a natural-language rationale grounded in optimizer output and the knowledge base.

## Requirements

### Requirement: In-house optimizer over walk-forward folds

The Parameter Optimizer SHALL be implemented in-house and SHALL evaluate every candidate parameter set across walk-forward folds defined by the strategy's objective spec. Optimizer evaluation MUST delegate to the Backtest Engine via the batch API.

#### Scenario: Walk-forward evaluation

- **WHEN** the optimizer evaluates a parameter set for a strategy with 5 walk-forward folds
- **THEN** the engine receives a batch with at least one run per fold and the optimizer's score is aggregated across folds per the objective spec

### Requirement: Search method selection

The optimizer SHALL support `grid`, `random`, and `bayesian` (Tree-structured Parzen Estimator) search methods, selectable per optimization run.

#### Scenario: Grid sweep over a small parameter space

- **WHEN** the optimizer is invoked with `method=grid` over a 3-parameter discrete space
- **THEN** every grid point is evaluated and the best per the objective is returned

#### Scenario: Bayesian search on an expensive backtest

- **WHEN** the optimizer is invoked with `method=bayesian, budget=N evaluations`
- **THEN** the optimizer runs at most N evaluations using TPE-driven proposals

### Requirement: Multi-metric objective consumption

The optimizer SHALL read the strategy's objective spec and apply the declared tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`). Hard-constraint secondary metrics MUST cause candidate rejection when violated.

#### Scenario: Constraint violation rejects a candidate

- **WHEN** a candidate's `max_drawdown` exceeds the constraint set in the objective spec
- **THEN** the optimizer rejects the candidate regardless of its primary metric value

### Requirement: Optimized output and rationale

The optimizer SHALL emit (a) an optimized parameter set together with its walk-forward aggregated metrics and (b) a natural-language rationale explaining why the chosen region of the parameter space is preferred. The rationale generator MUST consult the Knowledge Base in addition to optimizer output.

#### Scenario: Optimized output includes a rationale

- **WHEN** an optimization run completes
- **THEN** the result contains both the parameter set with metrics and a rationale string that references both optimizer-observed surface properties and KB citations

### Requirement: Determinism and seeding

Optimizer runs SHALL be deterministic given the same seed, search method, parameter space, and engine version. Random and Bayesian methods MUST use seeded RNGs.

#### Scenario: Replay a random search

- **WHEN** the same optimizer run is replayed with the same seed
- **THEN** the candidate sequence and final result are identical
