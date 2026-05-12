# Volatility Regimes and the VIX Term Structure

The VIX index measures the market-implied volatility of S&P 500 options over
the next thirty days. Its near-month future is the most liquid volatility
contract, but the curve extends further out and changes shape with regime.

## Contango vs Backwardation

When the VIX curve slopes upward — distant futures price above the spot index —
the curve is in contango. Contango is the default state; it reflects the cost
of carrying long volatility through quiet markets. VXX, an ETN that holds a
constant-maturity weighted basket of the front two VIX futures, loses value in
contango because each daily roll sells cheap front-month and buys more
expensive second-month exposure.

When realized volatility spikes the curve inverts: near-month futures price
above further-dated contracts. This is backwardation. VXX rallies sharply in
backwardation because the roll is now accretive. Backwardation rarely lasts;
mean reversion to contango is the dominant regime transition.

## Empirical Properties of the VIX Term Structure

- Backwardation episodes are short (median 4 trading days) and bursty.
- The transition from contango to backwardation is faster than the reverse.
- VXX's average annual decay in contango exceeds 70%, dwarfing dividend yield.
- Strategies that short VXX in contango must size for the tail risk of an
  abrupt backwardation flip.

## Strategy Implications

A volatility-range strategy that profits from VXX decay in contango must:

1. Avoid being short at the moment of regime flip.
2. Use a hedge (long-dated VIX call, long treasuries) for tail protection.
3. Adapt position size to realized volatility itself, not just the curve
   slope, since the slope can lag.

Regime detection is therefore the central problem. A vol regime classifier
that distinguishes low_vol, med_vol, and high_vol bands gives the strategy a
discrete state variable to switch on. Trend regimes (uptrend, chop, downtrend)
add a second axis useful for cross-asset hedges.
