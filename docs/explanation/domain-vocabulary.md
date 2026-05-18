# Domain vocabulary

Every term used across strategy-gpt's docs, defined once. Cross-referenced everywhere else.

## Strategy authoring

**Strategy** — Rust crate implementing the sealed `engine_rt::Strategy` trait. Authored either by hand or by the LLM through the build pipeline.

**Parameters** — typed knobs the strategy exposes; mutable without recompilation.

**Hypothesis** — named, human-readable claim that a specific change will move a target metric, with a falsification criterion.

**Bar** — OHLCV bar with UTC timestamp; atomic input to strategies.

**Modes** — engine backtest modes: `Plain`, `MonteCarlo { n, block_size }`, `Slippage { bps_grid }`, `RegimeFilter { ranges }`, `Sensitivity`.

## Metrics & objectives

**Metrics** — Sharpe, Sortino, Profit Factor, Win Ratio, Max Drawdown, Annualized Return, trade-length stats.

**Objective spec** — declarative, per-strategy: primary metric, secondary metrics with weights or hard constraints, tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`), fold configuration. Consumed by Evaluator and Optimizer uniformly. See [Objective spec reference](../reference/objective-spec.md).

**Fold scheme** — declarative split of an experiment slice into `count` (train, OOS) pairs. `rolling` slides equal-width windows; `anchored` pins train start to the slice start and lets train grow. Shared by `experiment-spec.folds` and `objectives.folds`.

**OOS aggregate** — score aggregator (currently `mean`) applied across folds' out-of-sample segments. The objective's `oos_min_score` is the OOS-gate threshold a candidate must clear.

## Experiments

**ExperimentSpec** — *user-facing* experiment envelope (`experiment-spec.yaml` / `.json`) consumed by `strategy-gpt run --spec`. Carries `artifact`, polymorphic `bars` (cache-resident `dataset` or auto-fetched `request`), `engine`, `runs`, `parallelism`, `caps`. See [experiment-spec reference](../reference/experiment-spec.md). Translates internally to a `BatchSpec` before submit.

**BatchSpec / RunSpec** — *internal* engine input across the PyO3 boundary. One strategy artifact, one dataset, many runs (parameters × modes × slices × seeds). Composed by the experiment-spec loader; not authored directly. See [BatchSpec reference](../reference/batch-spec.md).

## Optimization

**opt_id** — content-addressed identifier (blake2b of the canonical experiment-spec JSON) for a single optimization run. Names the persistence directory `ledger/optimizations/<opt_id>/`.

**Trial** — one backtest the optimizer commissioned: a row in `trials.parquet` carrying `(trial_id, round, phase, fold_index, params, seed, metrics, score, accepted, reject_reason, wall_secs)`.

**Fold winner** — the best-scoring accepted candidate for one fold's *train* search. Each fold yields exactly one winner.

**OOS aggregate** (selection sense) — mean of a fold winner's per-fold OOS metrics across all folds (`aggregator: mean` is the only supported aggregator). The final candidate is the fold winner with the best OOS-aggregate score, ties broken by lower per-fold OOS-score variance.

**Selection layer** — overfitting-aware gate + re-ranking that sits *above* the search method. Operates on `trials.parquet` + `manifest.json`; pure function of the trial set + knobs. Records `decision`, `pbo`, `deflated_sharpe`, `sensitivity_score`, and `would_have_picked` in `best.json`. Methodology in [Overfitting & selection](overfitting-and-selection.md).

**PBO** — Probability of Backtest Overfitting ([Bailey, Borwein, López de Prado, Zhu 2017](bibliography.md#bailey-borwein-lopez-de-prado-zhu-2017)). Computed by Combinatorially Symmetric Cross-Validation (CSCV) over the per-fold OOS metric matrix. If PBO exceeds the threshold (default 0.5), the run is `rejected_pbo` and no `best` is published without `--force`.

**DSR** — Deflated Sharpe Ratio ([Bailey & López de Prado 2014](bibliography.md#bailey-lopez-de-prado-2014)). Adjusts the raw Sharpe for multiple-testing inflation against the expected maximum under the null; reports the probability that the true Sharpe exceeds zero. Default final-rank metric.

**Robust score** — parameter-sensitivity score: `mean(score over k-NN neighborhood) − λ·std`. Reported by default; used for final ranking when `optimize.robust_objective: true` (or `--robust-objective`). Computed at selection time only; the search itself always sees the raw objective.

**Reselection** — post-hoc invocation of the selection layer over an existing `opt_id` with overridden knobs (`strategy-gpt optimize reselect <opt_id>`). Writes a new `best_<timestamp>.json` adjacent to the original; never overwrites.

## Hypothesis loop

**Decision log** — ledger record of accepted/rejected hypotheses with rationale; reloaded as context on subsequent loop runs.
