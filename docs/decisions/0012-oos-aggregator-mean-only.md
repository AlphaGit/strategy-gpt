# 0012 — Mean as the only OOS aggregator

## Context

The fold winners' per-fold OOS metrics must be combined into a single score for cross-fold ranking. Standard choices include the arithmetic mean, the median, the trimmed mean, and worst-case (minimum). Each carries different tradeoffs around robustness, variance, and interpretability.

## Decision

`aggregator: mean` is the **only** supported OOS aggregator. The objective spec's `aggregator` field accepts only `mean`; other values fail spec validation. Ties on the mean-aggregated score break by lower per-fold OOS-score variance.

## Consequences

- The selection layer's downstream computations (PBO, DSR, robust score) all assume the candidate-level score is the mean — keeping a single aggregator simplifies every downstream derivation.
- "Lower per-fold variance breaks ties" already captures the most-useful piece of what median / trimmed-mean would buy us, without changing the central tendency.
- Operators who want a robustness-aware ranking should reach for `--robust-objective` (see [Overfitting & selection](../explanation/overfitting-and-selection.md)) instead of swapping the aggregator.
- If a strategy class emerges where median aggregation is clearly preferable, a new ADR will admit it; the spec field exists today specifically to make the change additive.

## Alternatives Considered

- **Configurable aggregator.** Considered. Rejected because every downstream computation (DSR's max-Sharpe assumption, PBO's IS-best computation) would need a parallel implementation, multiplying complexity for a tradeoff most users do not need.
- **Median.** More robust to outlier folds but harder to deflate (DSR's machinery is in terms of mean Sharpe).

## Status

accepted
