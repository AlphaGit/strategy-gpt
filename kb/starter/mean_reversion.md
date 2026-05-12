# Mean Reversion in Equity Index Futures

Mean reversion strategies trade against short-term price extremes on the
expectation that prices return toward a running average. They work well when
volatility is bounded and trend signals are weak, and fail in persistent
trends or regime breaks.

## Canonical Signals

- **RSI (Relative Strength Index)**: an oscillator over recent gains and
  losses, ranging 0–100. RSI < 30 is "oversold"; RSI > 70 is "overbought".
- **Z-score of close to N-period EMA**: standardize the deviation of the close
  from a moving average. Signals fire at ±2σ.
- **ATR (Average True Range)** normalizes the deviation: trade only when the
  z-score exceeds, say, 1.5× current ATR.

## Why Mean Reversion Works in Equity Indices

- Equity indices are broad portfolios; idiosyncratic moves of constituents
  average out, leaving slower mean-reverting flows from rebalancing,
  options dealer hedging, and pension allocators.
- The dispersion of intraday moves is bounded by liquidity. Extreme moves
  attract counter-flow.

## Where It Fails

- Strong-trend regimes (e.g., 2020Q2 recovery, late-2022 inflation-driven
  selloffs) make mean reversion bleed for weeks.
- Vol-of-vol regime shifts: when volatility itself trends, oscillator
  thresholds need adaptive resets.
- Single-symbol strategies are exposed to event risk that breaks the mean.

## Walk-Forward Stability

Mean reversion parameter surfaces (RSI threshold, lookback, exit horizon)
tend to be flat near the optimum: many parameter combinations score similarly.
This is a virtue — it implies the strategy is not over-fit. Optimizers should
look for a plateau, not a peak.
