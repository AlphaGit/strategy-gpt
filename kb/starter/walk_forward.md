# Walk-Forward Validation for Trading Strategies

Walk-forward validation partitions a long historical sample into a sequence of
contiguous in-sample / out-of-sample folds. Each fold trains (or selects
parameters) on the in-sample window and evaluates on the immediately
following out-of-sample window. The aggregate out-of-sample performance is
the honest estimate of strategy quality.

## Why Walk-Forward Beats a Single Train/Test Split

- A single 70/30 split lets the modeler peek at the test window during
  iteration. Walk-forward repeats the split, so over-fit to any single
  out-of-sample window is unlikely to survive.
- The out-of-sample windows are contiguous and ordered. This preserves
  regime transitions that randomized cross-validation destroys.
- Aggregating Sharpe over folds yields a meaningful confidence interval,
  not a point estimate.

## Common Pitfalls

- **Look-ahead in features**: any indicator that uses bar `t+1` data must
  be lagged. Walk-forward does not protect against feature leakage.
- **Adaptive thresholds**: parameter values selected on the in-sample window
  must not be tuned on the out-of-sample window; that is hold-out leakage.
- **Selection bias across folds**: if a parameter set wins on the average
  out-of-sample fold but loses on every individual fold (averaging masks
  variance), the strategy is brittle.

## Fold Configuration

- **Anchored**: training window grows over time; each fold adds new history.
- **Rolling**: training window slides forward with fixed length.
- **Gapped**: a one-bar or one-day gap between train and test windows avoids
  spillover from intraday position carry.

Most equity-index strategies use 5–8 folds. Volatility strategies prefer
more folds (10+) because regime variance is higher and a small fold count can
miss a backwardation episode entirely.

## Objective Aggregation

The optimizer aggregates per-fold metrics into a single score per the
strategy's objective spec. Lexicographic mode picks the parameter set with
the best primary metric and lowest constraint violations. Weighted-sum
combines primary and secondary metrics with caller-specified weights. Pareto
mode returns the non-dominated frontier so the human picks the trade-off.
