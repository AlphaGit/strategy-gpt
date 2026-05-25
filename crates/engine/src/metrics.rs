//! Compute [`BacktestMetrics`] from the equity curve and closed trades.

use crate::result::{BacktestMetrics, EquityPoint, Trade};

/// Annualization factor for Sharpe/Sortino/return. 252 = daily bars; 12 =
/// monthly; etc. Strategies operating on intraday bars must pass an
/// appropriate factor (e.g., 252 * 6.5 * 60 for 1-minute bars).
pub fn compute_metrics(
    equity: &[EquityPoint],
    trades: &[Trade],
    annualization_factor: f64,
) -> BacktestMetrics {
    if equity.is_empty() {
        return BacktestMetrics::empty();
    }

    // No realized trades — the run never produced an actionable round-trip.
    // Sharpe / sortino / profit_factor / win_ratio / annualized_return all
    // reduce to "no information"; emit a zeroed shape rather than computing
    // derived ratios off a flat equity curve (which would still produce
    // 0.0 mechanically but obscures intent at the read site). `Trade`
    // unrealized positions are converted to closed trades upstream by
    // `TradeLog::close_remaining`, so an empty `trades` slice here means
    // the strategy genuinely never opened a position.
    if trades.is_empty() {
        return BacktestMetrics::empty();
    }

    let returns = bar_to_bar_returns(equity);
    let sharpe = ratio_annualized(&returns, |_| true, annualization_factor);
    let sortino = ratio_annualized(&returns, |r| *r < 0.0, annualization_factor);

    let max_drawdown = equity.iter().map(|p| p.drawdown).fold(0.0_f64, f64::max);

    let annualized_return = annualized_return(equity, annualization_factor);

    let n_trades = trades.len() as u32;
    let (gross_win, gross_loss) =
        trades
            .iter()
            .map(|t| t.pnl)
            .fold((0.0_f64, 0.0_f64), |(w, l), p| {
                if p > 0.0 {
                    (w + p, l)
                } else {
                    (w, l + p.abs())
                }
            });
    // All-winners edge case: `gross_loss == 0` would yield `+inf`, which
    // (a) serializes as JSON `null` (downstream typed deserializers reject)
    // and (b) propagates as the `f64::MAX` sentinel through aggregators
    // like `_aggregate_mean` and renders as a 300-digit number. Substitute
    // a token $0.01 "loss" so the ratio is finite, monotonic in `gross_win`,
    // and readable. With `gross_win = 0` the ratio is 0 regardless.
    const GROSS_LOSS_FLOOR_USD: f64 = 0.01;
    let denom = if gross_loss > 0.0 {
        gross_loss
    } else {
        GROSS_LOSS_FLOOR_USD
    };
    let profit_factor = gross_win / denom;

    let wins = trades.iter().filter(|t| t.pnl > 0.0).count() as f64;
    let win_ratio = if n_trades > 0 {
        wins / n_trades as f64
    } else {
        0.0
    };

    // Trade length expressed in raw seconds; callers rescale by their bar
    // resolution to get bar counts.
    let avg_trade_length_bars = if n_trades > 0 {
        let total: f64 = trades
            .iter()
            .map(|t| (t.exit_ts - t.entry_ts).num_seconds().max(0) as f64)
            .sum();
        total / n_trades as f64
    } else {
        0.0
    };

    BacktestMetrics {
        sharpe: finite_or_zero(sharpe),
        sortino: finite_or_zero(sortino),
        profit_factor: finite_or_zero(profit_factor),
        win_ratio: finite_or_zero(win_ratio),
        max_drawdown: finite_or_zero(max_drawdown),
        annualized_return: finite_or_zero(annualized_return),
        n_trades,
        avg_trade_length_bars: finite_or_zero(avg_trade_length_bars),
    }
}

/// Map any non-finite (`NaN` / `±inf`) value to `0.0`. serde_json serializes
/// non-finite f64 as JSON `null`, which downstream typed deserializers reject
/// with a wire-protocol error. Funnel every metric through this guard so the
/// wire stays valid even when degenerate inputs (zero trades, zero variance,
/// single-bar equity) hit an unguarded computation path.
fn finite_or_zero(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else if v == f64::INFINITY {
        f64::MAX
    } else if v == f64::NEG_INFINITY {
        f64::MIN
    } else {
        0.0
    }
}

fn bar_to_bar_returns(equity: &[EquityPoint]) -> Vec<f64> {
    let mut out = Vec::with_capacity(equity.len().saturating_sub(1));
    for w in equity.windows(2) {
        let a = w[0].equity;
        let b = w[1].equity;
        if a == 0.0 {
            continue;
        }
        out.push((b - a) / a);
    }
    out
}

fn ratio_annualized(
    returns: &[f64],
    include: impl Fn(&f64) -> bool,
    annualization_factor: f64,
) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }
    let n = returns.len() as f64;
    let mean = returns.iter().sum::<f64>() / n;
    let filtered: Vec<f64> = returns.iter().copied().filter(|r| include(r)).collect();
    if filtered.is_empty() {
        return 0.0;
    }
    let m = filtered.len() as f64;
    let mean_f = filtered.iter().sum::<f64>() / m;
    let var = filtered.iter().map(|r| (*r - mean_f).powi(2)).sum::<f64>() / m;
    let stdev = var.sqrt();
    if stdev == 0.0 {
        return 0.0;
    }
    (mean / stdev) * annualization_factor.sqrt()
}

fn annualized_return(equity: &[EquityPoint], annualization_factor: f64) -> f64 {
    let first = equity.first().expect("non-empty checked");
    let last = equity.last().expect("non-empty checked");
    if first.equity <= 0.0 || equity.len() < 2 {
        return 0.0;
    }
    let total_ret = last.equity / first.equity;
    let bars = (equity.len() - 1) as f64;
    if bars <= 0.0 {
        return 0.0;
    }
    total_ret.powf(annualization_factor / bars) - 1.0
}
