# Spec: objectives

## MODIFIED Requirements

### Requirement: Per-strategy declarative objective spec

Each strategy SHALL declare a per-strategy objective specification. The spec MUST include a `primary` metric with target and weight, zero or more `secondary` metrics each with target, weight, and `mode` (`constraint` or `soft`), a `tradeoff` selector (`lexicographic`, `weighted_sum`, or `pareto`), and a `folds` block defining the fold scheme used to evaluate candidates.

#### Scenario: Spec drives evaluator and optimizer uniformly

- **WHEN** the Evaluator and the Parameter Optimizer both consume a strategy's objective spec
- **THEN** they apply identical rules for metric targets, weights, constraints, and tradeoff handling

### Requirement: Fold configuration

The objective spec SHALL declare a `folds` block containing `count`, optional `gap`, and `oos_min_score` (minimum aggregated out-of-sample score for a candidate to be considered acceptable). The block's structural fields (`count`, `scheme`, `gap`, `warmup_bars`) MUST match the experiment-spec `folds` block schema; `oos_min_score` is specific to the objective.

#### Scenario: OOS gate

- **WHEN** a candidate's OOS aggregate score across folds falls below `oos_min_score`
- **THEN** the candidate is rejected by the optimizer regardless of in-sample performance

#### Scenario: Legacy walk_forward key rejected

- **WHEN** an objective spec declares the legacy top-level `walk_forward` block instead of `folds`
- **THEN** spec validation fails with a structured migration error naming the new key

## REMOVED Requirements

### Requirement: Walk-forward configuration

**Reason**: Renamed and restructured under `Fold configuration` above. The term "walk-forward" is dropped from all spec surfaces in favor of "folds" and "OOS aggregate" to align the objectives, experiment-spec, and optimizer vocabularies.

**Migration**: Rename the top-level `walk_forward:` block in every objective YAML to `folds:`. The fields inside (`count`, `gap`, `scheme`, `oos_min_score`) carry over unchanged.
