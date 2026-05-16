# Optimize command — per-fold search, recursive grid, benchmark

## Why

The `param-optimizer` capability promised in the original rewrite is described as evaluating candidates across walk-forward folds with grid/random/Bayesian search and emitting a single optimized parameter set. Two refinements have emerged from product use:

1. **Per-fold search, not OOS-only evaluation.** Searching once over the full slice and aggregating OOS is convenient but allows a candidate to win by overfitting to a single regime that dominates the slice. Per-fold search — fit the parameter on the *train* segment of each fold, then validate on its OOS segment — generates F candidate-best sets that explicitly reflect different regimes. The "final" parameter set is then the one with the best OOS aggregate among those fold winners.
2. **Recursive grid as the default method.** TPE and random search are valuable as alternatives, but the platform's primary use case is research where users want to *see* the loss surface. Recursive grid (coarse pass → refine top cells → repeat) is deterministic, parallelizable per round, and produces a visualizable surface as a side effect.

Additionally, this change introduces:

- `strategy-gpt optimize --spec experiment.yaml` CLI command, replacing the not-implemented stub.
- `--benchmark` mode that samples a handful of candidates, measures wall time, and predicts total runtime before launching.
- Persistence layout sized for millions of trials: a parquet trial table + a SQLite index per optimization run, with replay-by-trial.

The "walk-forward" terminology is dropped (`2026-05-14-optimization-spec` covers the rename); this change uses "folds" and "OOS aggregate" throughout.

## What Changes

- **MODIFIED capability** `param-optimizer`:
  - Replace "In-house optimizer over walk-forward folds" with per-fold train search + OOS validation requirement.
  - Add `recursive_grid` as a search method alongside `grid`, `random`, `bayesian`.
  - Add a plateau-stop convergence criterion (per-dim ε, full-stop only when *all* dimensions converge).
  - Add `--benchmark` requirement (sample, measure, predict).
  - Add `parallelism: auto` resolution requirement (`max(1, usable_cpus - 1)`).
  - Add persistence requirement: per-optimization manifest, parquet trial table, SQLite index, replay-by-trial-id.
  - Final-selection requirement: among fold winners, pick the one with the best OOS-aggregate score (`aggregator: mean` for v1).
  - Drop "walk-forward" wording from every requirement.
  - Rationale generation requirement defers to a follow-up change; this change does not produce LLM-grounded rationale.
- New CLI subcommands:
  - `strategy-gpt optimize --spec experiment.yaml [--benchmark] [--method ...] [--persist-root ...]`
  - `strategy-gpt optimize inspect <opt_id> [--trial <trial_id>]`
  - `strategy-gpt optimize replay <opt_id> --trial <trial_id>`
- Engine batch-packing (per the prereq change) is used to dispatch each search round as a single packed batch.
- Reference example `examples/vxx/optimize.py` is deleted; the experiment-spec for VXX gains an `optimize` block as the new starting point.

## Capabilities

### Modified Capabilities

- `param-optimizer`: per-fold search, recursive grid, benchmark, parallelism auto, persistence, drop walk-forward wording, drop in-this-change rationale requirement.

## Impact

- **Code**:
  - `python/strategy_gpt/optimizer.py` — add `RecursiveGridSearcher`; refactor driver for per-fold (train-search → OOS-validate) flow; add fold-winner aggregator.
  - `python/strategy_gpt/optimization_runner.py` (new) — orchestrates per-fold search rounds, packs rounds into single engine batches, writes parquet + sqlite.
  - `python/strategy_gpt/cli.py` — implement `optimize`, `optimize inspect`, `optimize replay`.
  - `python/strategy_gpt/benchmark.py` (new) — sample + predict.
  - `ledger/optimizations/` filesystem layout.
- **Tests**:
  - Per-fold flow on a 2-param synthetic objective with known optimum per fold.
  - Recursive grid plateau-stop correctness.
  - Benchmark prediction accuracy under fixed per-run cost.
  - Persistence + replay round-trip on a small run.
- **Dependencies**: `pyarrow` (parquet) — already pulled in via polars; verify direct dep.
- **Reference example**: `examples/vxx/optimize.py` deleted; `examples/vxx/experiment.yaml` gains an `optimize` block.
- **Out of scope (this change)**:
  - LLM-grounded rationale generation (follow-up).
  - Search methods beyond recursive grid + the existing grid/random/TPE (covered in a follow-up `additional-search-methods` change).
  - Stress modes applied during search (deferred to a stress-during-optimization change).
  - Live progress UI / web dashboard.
