# Spec: objectives

## Purpose

Declarative per-strategy multi-metric objective specification consumed uniformly by the Evaluator and Parameter Optimizer. Defines primary and secondary metrics with targets, weights, constraint vs soft handling, tradeoff mode (lexicographic, weighted_sum, pareto), and fold configuration so candidate evaluation is consistent across the system.

## Requirements

### Requirement: Per-strategy declarative objective spec

Each strategy SHALL declare a per-strategy objective specification. The spec MUST include a `primary` metric with target and weight, zero or more `secondary` metrics each with target, weight, and `mode` (`constraint` or `soft`), a `tradeoff` selector (`lexicographic`, `weighted_sum`, or `pareto`), and a `folds` block defining the fold scheme used to evaluate candidates.

#### Scenario: Spec drives evaluator and optimizer uniformly

- **WHEN** the Evaluator and the Parameter Optimizer both consume a strategy's objective spec
- **THEN** they apply identical rules for metric targets, weights, constraints, and tradeoff handling

### Requirement: Constraint vs soft secondary metrics

Secondary metrics in `mode: constraint` SHALL hard-fail any candidate that violates them, regardless of primary metric value. Secondary metrics in `mode: soft` contribute to the score per `tradeoff` mode.

#### Scenario: Hard constraint violation

- **WHEN** a candidate violates a `max_drawdown <= 0.20` constraint
- **THEN** the candidate is rejected even if its Sharpe is the highest in the search

#### Scenario: Soft secondary contribution

- **WHEN** a soft secondary metric falls short of its target under `tradeoff: weighted_sum`
- **THEN** the candidate's score is reduced by the configured weight rather than rejected

### Requirement: Tradeoff modes

The optimizer SHALL support `lexicographic` (optimize primary, break ties on secondary), `weighted_sum` (scalarize across all soft metrics), and `pareto` (return the frontier rather than a single best).

#### Scenario: Pareto returns frontier

- **WHEN** the spec's `tradeoff` is `pareto`
- **THEN** the optimizer returns a non-dominated set of candidates rather than a single best parameter set

### Requirement: Fold configuration

The objective spec SHALL declare a `folds` block containing `count`, optional `gap`, and `oos_min_score` (minimum aggregated out-of-sample score for a candidate to be considered acceptable). The block's structural fields (`count`, `scheme`, `gap`, `warmup_bars`) MUST match the experiment-spec `folds` block schema; `oos_min_score` is specific to the objective.

#### Scenario: OOS gate

- **WHEN** a candidate's OOS aggregate score across folds falls below `oos_min_score`
- **THEN** the candidate is rejected by the optimizer regardless of in-sample performance

#### Scenario: Legacy walk_forward key rejected

- **WHEN** an objective spec declares the legacy top-level `walk_forward` block instead of `folds`
- **THEN** spec validation fails with a structured migration error naming the new key

### Requirement: Spec validation

Objective specs SHALL be validated for self-consistency before use: every named metric must be one the engine emits; every constraint must be a valid comparison; weights must be non-negative; `pareto` mode requires at least two contributing metrics.

#### Scenario: Invalid metric name

- **WHEN** an objective spec references a metric name the engine does not emit
- **THEN** spec validation fails with a structured error before any backtest runs
